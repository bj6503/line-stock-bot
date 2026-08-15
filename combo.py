import os
import requests
import datetime
import time

from state import load_state
from bb_squeeze import get_twse_daily, recent_weekdays

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

LINE_MAX_LEN = 4800
SURGE_PCT = 4.0
PLUNGE_PCT = -4.0
NEAR_PCT = 1.0
MIN_PRICE = 30.0
MIN_VOLUME = 1000


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


def get_two_sessions() -> tuple:
    """一次抓出相鄰的兩個交易日行情，確保漲跌幅正確"""
    sessions = []
    for d in reversed(recent_weekdays(10)):
        m = get_twse_daily(d)
        if m:
            sessions.append((d, m))
            print(f"  取得 {d}：{len(m)} 筆")
            if len(sessions) >= 2:
                break
        time.sleep(0.9)

    if len(sessions) < 2:
        return (None, {}, None, {})

    (d_now, m_now), (d_prev, m_prev) = sessions[0], sessions[1]
    return (d_now, m_now, d_prev, m_prev)


def check_watch(watch: list, market: dict, prev: dict) -> dict:
    """比對早盤清單目前狀況"""
    alerts = {"break_down": [], "near_resist": [], "surge": [], "normal": []}

    for w in watch:
        code = w["code"]
        cur = market.get(code)
        if not cur or cur.get("close", 0) <= 0:
            continue
        price = cur["close"]

        p = prev.get(code)
        base = p["close"] if p and p.get("close", 0) > 0 else (w.get("ref_price") or price)
        chg = (price - base) / base * 100 if base > 0 else 0

        item = {
            "code": code, "name": w["name"], "source": w.get("source", ""),
            "price": price, "chg": chg,
            "high": cur.get("high", price), "low": cur.get("low", price),
        }

        start = w.get("start_price")
        sup = w.get("support")
        sup2 = w.get("support2")
        res = w.get("resistance")

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


def scan_moves(market: dict, prev: dict, exclude: set) -> dict:
    """全市場急漲急跌掃描"""
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
        if price < MIN_PRICE:
            continue
        vol = cur.get("volume", 0)
        if vol < MIN_VOLUME:
            continue

        chg = (price - p["close"]) / p["close"] * 100
        prev_vol = p.get("volume", 0)
        vol_ratio = vol / prev_vol if prev_vol > 0 else 0

        item = {"code": code, "name": cur.get("name", code),
                "price": price, "chg": chg, "vol_ratio": vol_ratio,
                "high": cur.get("high", price), "low": cur.get("low", price)}

        if chg >= SURGE_PCT and vol_ratio >= 1.5:
            surge.append(item)
        elif chg <= PLUNGE_PCT and vol_ratio >= 2.0:
            # 是否有下影線收回（急殺後買盤承接）
            rng = item["high"] - item["low"]
            if rng > 0:
                item["recover"] = (price - item["low"]) / rng * 100
            else:
                item["recover"] = 0
            plunge.append(item)

    surge.sort(key=lambda x: -x["chg"])
    plunge.sort(key=lambda x: x["chg"])
    return {"surge": surge[:5], "plunge": plunge[:5]}


def build_message(state: dict, alerts: dict, moves: dict, date_str: str) -> str:
    now = tw_now()
    d = f"{date_str[4:6]}/{date_str[6:]}" if date_str else now.strftime("%m/%d")
    lines = [f"📕 {d} 收盤檢討", ""]

    if state.get("verdict"):
        lines.append(f"{state.get('verdict_icon', '')} 今日環境：{state['verdict']}")
        lines.append("")

    if alerts.get("break_down"):
        lines.append("🚨 跌破警示")
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

    if moves.get("surge"):
        lines.append("🚀 今日強勢（非早盤名單）")
        lines.append("─" * 16)
        for x in moves["surge"]:
            lines.append(f"  {x['name']} {x['code']} {x['price']:.1f}"
                         f"（{x['chg']:+.1f}%）量{x['vol_ratio']:.1f}倍")
        lines.append("")

    if moves.get("plunge"):
        lines.append("💥 急跌爆量（恐慌訊號）")
        lines.append("─" * 16)
        for x in moves["plunge"]:
            rec = x.get("recover", 0)
            tag = "✅有承接" if rec >= 50 else "⚠️收最低" if rec <= 20 else ""
            lines.append(f"  {x['name']} {x['code']} {x['price']:.1f}"
                         f"（{x['chg']:+.1f}%）量{x['vol_ratio']:.1f}倍 {tag}")
        lines.append("   ※收回>50%代表有買盤接手，可留意")
        lines.append("   ※收在最低則續弱，避開")
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

    print("=== 取得相鄰兩交易日行情 ===")
    d_now, market, d_prev, prev = get_two_sessions()
    if not market or not prev:
        print("行情取得失敗")
        return
    print(f"  最新 {d_now} vs 前日 {d_prev}")

    print("=== 比對早盤清單 ===")
    alerts = check_watch(watch, market, prev) if watch else {}

    print("=== 全市場掃描 ===")
    moves = scan_moves(market, prev, {w["code"] for w in watch})

    msg = build_message(state, alerts, moves, d_now)
    if not msg:
        print("無重要訊息，不推播")
        return

    print(msg)
    print("=== 推播 ===")
    send_line_message(msg)
    print("完成")


if __name__ == "__main__":
    main()
