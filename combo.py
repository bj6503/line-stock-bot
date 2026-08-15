import os
import requests
import datetime
import time

from state import load_state
from bb_squeeze import get_twse_daily, recent_weekdays, clean_name, to_num

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

LINE_MAX_LEN = 4800
SURGE_PCT = 4.0        # 盤中急漲門檻
PLUNGE_PCT = -4.0      # 盤中急跌門檻
NEAR_PCT = 1.0         # 接近關卡的距離


def tw_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def split_message(text: str) -> list:
    if len(text) <= LINE_MAX_LEN:
        return [text]
    chunks, cur, size = [], [], 0
    for line in text.split("\n"):
        if size + len(line) + 1 > LINE_MAX_LEN and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def send_line_message(text: str):
    headers = {
        "Authorization": "Bearer " + LINE_TOKEN,
        "Content-Type": "application/json"
    }
    for uid in [u.strip() for u in LINE_USER_ID.split(",") if u.strip()]:
        for i, chunk in enumerate(split_message(text)):
            body = {"to": uid, "messages": [{"type": "text", "text": chunk}]}
            r = requests.post("https://api.line.me/v2/bot/message/push",
                              json=body, headers=headers)
            print(f"推播 {uid[:8]}... ({i+1}) {r.status_code}")
            if r.status_code != 200:
                print(r.text)


def get_latest_market() -> dict:
    """取得最新一日全市場行情（盤中為即時累計）"""
    for d in reversed(recent_weekdays(6)):
        m = get_twse_daily(d)
        if m:
            print(f"  取得 {d} 行情 {len(m)} 筆")
            return m
        time.sleep(0.8)
    return {}


def get_prev_market(skip_date_count: int = 2) -> dict:
    """取得前一交易日行情，用來算漲跌"""
    got = 0
    for d in reversed(recent_weekdays(8)):
        m = get_twse_daily(d)
        if m:
            got += 1
            if got == skip_date_count:
                return m
        time.sleep(0.8)
    return {}


def check_watch(watch: list, market: dict) -> dict:
    """比對早盤清單目前狀況"""
    alerts = {"break_down": [], "near_resist": [], "surge": [], "normal": []}

    for w in watch:
        code = w["code"]
        cur = market.get(code)
        if not cur or cur.get("close", 0) <= 0:
            continue
        price = cur["close"]
        ref = w.get("ref_price") or price
        chg = (price - ref) / ref * 100 if ref > 0 else 0

        item = {
            "code": code, "name": w["name"], "source": w.get("source", ""),
            "price": price, "ref": ref, "chg": chg,
            "high": cur.get("high", price), "low": cur.get("low", price),
        }

        start = w.get("start_price")
        sup = w.get("support")
        sup2 = w.get("support2")
        res = w.get("resistance")

        # 跌破起漲點 or 支撐
        if start and price < start:
            item["reason"] = f"跌破起漲點{start:.1f}"
            item["next"] = sup2 or sup
            alerts["break_down"].append(item)
            continue
        if sup and price < sup:
            item["reason"] = f"跌破支撐{sup:.1f}"
            item["next"] = sup2
            alerts["break_down"].append(item)
            continue

        # 逼近或突破壓力
        if res:
            gap = (res - price) / price * 100
            if gap <= 0:
                item["reason"] = f"突破壓力{res:.1f}"
                item["broke"] = True
                alerts["near_resist"].append(item)
                continue
            elif gap <= NEAR_PCT:
                item["reason"] = f"逼近壓力{res:.1f}（差{gap:.1f}%）"
                item["broke"] = False
                alerts["near_resist"].append(item)
                continue

        if chg >= SURGE_PCT:
            alerts["surge"].append(item)
        else:
            alerts["normal"].append(item)

    return alerts


def scan_intraday(market: dict, prev: dict, exclude: set) -> dict:
    """盤中掃描：急漲急跌"""
    surge, plunge = [], []
    for code, cur in market.items():
        if code in exclude or len(code) != 4 or code.startswith("00"):
            continue
        if code.startswith("28") or code == "5880":
            continue
        p = prev.get(code)
        if not p or p.get("close", 0) <= 0:
            continue
        price = cur.get("close", 0)
        if price < 30:
            continue
        vol = cur.get("volume", 0)
        if vol < 1000:
            continue
        chg = (price - p["close"]) / p["close"] * 100
        prev_vol = p.get("volume", 0)
        vol_ratio = vol / prev_vol if prev_vol > 0 else 0

        item = {"code": code, "name": cur.get("name", code),
                "price": price, "chg": chg, "vol_ratio": vol_ratio}

        if chg >= SURGE_PCT and vol_ratio >= 1.5:
            surge.append(item)
        elif chg <= PLUNGE_PCT and vol_ratio >= 2.0:
            plunge.append(item)

    surge.sort(key=lambda x: -x["chg"])
    plunge.sort(key=lambda x: x["chg"])
    return {"surge": surge[:5], "plunge": plunge[:5]}


def build_message(state: dict, alerts: dict, intraday: dict) -> str:
    now = tw_now()
    lines = [f"⏰ {now.strftime('%m/%d %H:%M')} 盤中追蹤", ""]

    if state.get("verdict"):
        lines.append(f"{state.get('verdict_icon', '')} 今日環境：{state['verdict']}")
        lines.append("")

    if alerts.get("break_down"):
        lines.append("🚨 跌破警示（考慮出場）")
        lines.append("═" * 16)
        for a in alerts["break_down"]:
            lines.append(f"🔴 {a['name']} {a['code']}  {a['price']:.1f}（{a['chg']:+.1f}%）")
            lines.append(f"   {a['reason']}")
            if a.get("next"):
                lines.append(f"   下一支撐 {a['next']:.1f}")
        lines.append("")

    if alerts.get("near_resist"):
        lines.append("🎯 壓力關卡")
        lines.append("═" * 16)
        for a in alerts["near_resist"]:
            icon = "🟢" if a.get("broke") else "🟡"
            lines.append(f"{icon} {a['name']} {a['code']}  {a['price']:.1f}（{a['chg']:+.1f}%）")
            lines.append(f"   {a['reason']}")
        lines.append("")

    if alerts.get("surge"):
        lines.append("📈 早盤標的走強")
        for a in alerts["surge"]:
            lines.append(f"  {a['name']} {a['code']} {a['price']:.1f}（{a['chg']:+.1f}%）")
        lines.append("")

    if intraday.get("surge"):
        lines.append("🚀 盤中新急漲（非早盤名單）")
        lines.append("─" * 16)
        for x in intraday["surge"]:
            lines.append(f"  {x['name']} {x['code']} {x['price']:.1f}"
                         f"（{x['chg']:+.1f}%）量{x['vol_ratio']:.1f}倍")
        lines.append("")

    if intraday.get("plunge"):
        lines.append("💥 盤中急跌爆量（恐慌訊號）")
        lines.append("─" * 16)
        for x in intraday["plunge"]:
            lines.append(f"  {x['name']} {x['code']} {x['price']:.1f}"
                         f"（{x['chg']:+.1f}%）量{x['vol_ratio']:.1f}倍")
        lines.append("   ※急殺爆量可能是獵殺停損")
        lines.append("   若後續收回可留意，續跌則避開")
        lines.append("")

    if alerts.get("normal"):
        lines.append("➖ 其餘早盤標的")
        for a in alerts["normal"][:8]:
            lines.append(f"  {a['name']} {a['code']} {a['price']:.1f}（{a['chg']:+.1f}%）")

    body = "\n".join(lines).strip()
    if body.count("\n") < 4:
        return ""

    return body + "\n\n⚠️ 僅供參考，請自行判斷風險"


def main():
    force = os.environ.get("FORCE_RUN", "").lower() == "true"
    if not force and tw_now().weekday() >= 5:
        print("週末不執行")
        return

    print("=== 讀取早盤記錄 ===")
    state = load_state()
    watch = state.get("watch", [])
    print(f"  追蹤 {len(watch)} 支")

    print("=== 取得最新行情 ===")
    market = get_latest_market()
    if not market:
        print("行情取得失敗")
        return

    print("=== 取得前日行情 ===")
    prev = get_prev_market()

    print("=== 比對早盤清單 ===")
    alerts = check_watch(watch, market) if watch else {}

    print("=== 盤中掃描 ===")
    watch_codes = {w["code"] for w in watch}
    intraday = scan_intraday(market, prev, watch_codes) if prev else {}

    msg = build_message(state, alerts, intraday)
    if not msg:
        print("無重要訊息，不推播")
        return

    print(msg)
    print("=== 推播 ===")
    send_line_message(msg)
    print("完成")


if __name__ == "__main__":
    main()
