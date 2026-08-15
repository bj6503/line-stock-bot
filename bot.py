import os
import requests
import datetime

from market_env import build_env, format_env
from daily_picks import get_daily_picks, stars_str
from risk_map import build_risk_map

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

RISK_TARGETS = 3  # 對前幾名跑風險地圖


def tw_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def send_line_message(text: str):
    headers = {
        "Authorization": "Bearer " + LINE_TOKEN,
        "Content-Type": "application/json"
    }
    for uid in [u.strip() for u in LINE_USER_ID.split(",") if u.strip()]:
        body = {"to": uid, "messages": [{"type": "text", "text": text}]}
        r = requests.post("https://api.line.me/v2/bot/message/push",
                          json=body, headers=headers)
        print(f"推播 {uid[:8]}... {r.status_code}")
        if r.status_code != 200:
            print(r.text)


def pick_risk_targets(picks: dict) -> list:
    """挑出要做風險地圖的標的：雙主力優先，補足外資/投信高星"""
    targets = []
    seen = set()

    for x in picks.get("both", []):
        if x["code"] not in seen:
            targets.append(x)
            seen.add(x["code"])

    pool = picks.get("foreign", []) + picks.get("trust", [])
    pool.sort(key=lambda x: -x.get("stars", 0))
    for x in pool:
        if len(targets) >= RISK_TARGETS:
            break
        if x["code"] not in seen:
            targets.append(x)
            seen.add(x["code"])

    return targets[:RISK_TARGETS]


def format_stock_line(x: dict, show_trust: bool = False) -> list:
    lines = [f"{stars_str(x.get('stars', 1))} {x['name']} {x['code']}"]
    if show_trust and x.get("trust_net") is not None:
        lines.append(f"   外資+{x['net']:.0f} 投信+{x['trust_net']:.0f}張 連{x['streak']}日")
    else:
        lines.append(f"   +{x['net']:.0f}張 連{x['streak']}日 佔量{x['ratio']:.0f}%")
    lines.append(f"   現價{x['close']:.1f} 波動{x['atr']:.1f}% 振幅{x['range']:.0f}%")
    return lines


def format_risk_brief(risk: dict) -> list:
    """精簡版風險地圖"""
    if not risk:
        return []
    cur = risk["current"]
    m = risk.get("margin", {})
    lv = risk.get("levels", {})
    lines = []

    pnl = risk.get("margin_pnl")
    if pnl is not None:
        if pnl > 5:
            lines.append(f"   ⚠️ 融資獲利{pnl:+.1f}%，回檔恐了結")
        elif pnl < -5:
            lines.append(f"   ✅ 融資套牢{pnl:+.1f}%，賣壓已釋放")

    if m.get("warn_price"):
        wp = m["warn_price"]
        lines.append(f"   🟡 融資警戒 {wp:.1f}（{(wp-cur)/cur*100:+.1f}%）")

    if lv.get("low_30"):
        lo = lv["low_30"]
        lines.append(f"   📉 前波低 {lo:.1f}（{(lo-cur)/cur*100:+.1f}%）")

    for z in risk.get("danger_zones", [])[:1]:
        lines.append(f"   ⚠️ 關鍵防線 {z['price']:.1f}（{z['pct']:+.1f}%）")
        lines.append(f"      {z['reasons'][0]}×{z['reasons'][1]}")

    if m.get("recent_change") is not None:
        rc = m["recent_change"]
        if rc > 10:
            lines.append(f"   🔥 融資{rc:+.0f}% 火藥累積")
        elif rc < -10:
            lines.append(f"   ✅ 融資{rc:+.0f}% 籌碼沉澱")

    return lines


def build_message(env: dict, picks: dict, risks: dict) -> str:
    now = tw_now()
    lines = [f"📊 {now.strftime('%m/%d')} 盤前情報", ""]

    # 環境
    lines.append(format_env(env))
    lines.append("")

    if not picks:
        lines.append("⚠️ 主力資料取得失敗")
        return "\n".join(lines)

    d = picks["date"]
    lines.append(f"📋 {d[4:6]}/{d[6:]} 主力動向")
    lines.append("═" * 16)

    if picks.get("both"):
        lines.append("🔥 雙主力同買")
        for x in picks["both"]:
            lines.extend(format_stock_line(x, show_trust=True))
            if x["code"] in risks:
                lines.extend(format_risk_brief(risks[x["code"]]))
        lines.append("─" * 16)

    if picks.get("foreign"):
        lines.append("🏦 外資買超")
        for x in picks["foreign"]:
            lines.extend(format_stock_line(x))
            if x["code"] in risks:
                lines.extend(format_risk_brief(risks[x["code"]]))
        lines.append("─" * 16)

    if picks.get("trust"):
        lines.append("🎯 投信買超")
        for x in picks["trust"]:
            lines.extend(format_stock_line(x))
            if x["code"] in risks:
                lines.extend(format_risk_brief(risks[x["code"]]))

    if picks.get("etf"):
        lines.append("─" * 16)
        lines.append("📊 ETF資金")
        for x in picks["etf"]:
            lines.append(f"  {x['name']} {x['code']} +{x['net']:.0f}張")

    lines.append("")
    lines.append("⚠️ 僅供參考，請自行判斷風險")
    return "\n".join(lines)


def main():
    if tw_now().weekday() >= 5:
        print("週末不執行")
        return

    print("=== 環境判讀 ===")
    env = build_env()

    print("=== 主力動向 ===")
    picks = get_daily_picks()

    print("=== 風險地圖 ===")
    risks = {}
    if picks:
        for t in pick_risk_targets(picks):
            print(f"  分析 {t['name']} {t['code']}")
            r = build_risk_map(t["code"], t["name"])
            if r:
                risks[t["code"]] = r

    print("=== 組合訊息 ===")
    msg = build_message(env, picks, risks)
    print(msg)

    print("=== 推播 ===")
    send_line_message(msg)
    print("完成")


if __name__ == "__main__":
    main()
