import os
import requests
import datetime

from market_env import build_env, format_env
from daily_picks import get_daily_picks, stars_str
from risk_map import build_risk_map
from bb_squeeze import scan_market, find_golden

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

RISK_TARGETS = 5
LINE_MAX_LEN = 4800


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
    chunks = split_message(text)
    for uid in [u.strip() for u in LINE_USER_ID.split(",") if u.strip()]:
        for i, chunk in enumerate(chunks):
            body = {"to": uid, "messages": [{"type": "text", "text": chunk}]}
            r = requests.post("https://api.line.me/v2/bot/message/push",
                              json=body, headers=headers)
            print(f"推播 {uid[:8]}... ({i+1}/{len(chunks)}) {r.status_code}")
            if r.status_code != 200:
                print(r.text)


def squeeze_text(bb: dict) -> str:
    """統一壓縮天數的說法"""
    sq = bb.get("squeeze_before", 0) or bb.get("squeeze_days", 0)
    return f"壓縮{sq}日後發動" if sq > 0 else "剛脫離壓縮"


def pick_risk_targets(picks: dict, golden: list) -> list:
    targets, seen = [], set()

    for g in golden:
        if g["code"] not in seen:
            targets.append({"code": g["code"], "name": g["name"]})
            seen.add(g["code"])

    for x in picks.get("both", []):
        if len(targets) >= RISK_TARGETS:
            break
        if x["code"] not in seen:
            targets.append({"code": x["code"], "name": x["name"]})
            seen.add(x["code"])

    pool = picks.get("trust", []) + picks.get("foreign", [])
    pool.sort(key=lambda x: -x.get("stars", 0))
    for x in pool:
        if len(targets) >= RISK_TARGETS:
            break
        if x["code"] not in seen:
            targets.append({"code": x["code"], "name": x["name"]})
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
    if not risk:
        return []
    res = risk.get("ladder", {}).get("resistance", [])
    sup = risk.get("ladder", {}).get("support", [])
    rr = risk.get("rr", {})
    burst = risk.get("burst", {})
    lines = []

    if burst.get("is_burst"):
        lines.append(f"   ⚡ 起漲點 {burst['start_price']:.1f}（跌破視同失敗）")

    if res:
        r0 = res[0]
        mark = "※" if r0.get("estimated") else ""
        lines.append(f"   📈 壓力 {r0['price']:.1f}（{r0['pct']:+.1f}%）{mark}")
    if sup:
        s0 = sup[0]
        lines.append(f"   📉 支撐 {s0['price']:.1f}（{s0['pct']:+.1f}%）")
        if len(sup) >= 2:
            lines.append(f"      跌破→直落{sup[1]['price']:.1f}")

    if rr.get("value") is not None:
        v, conf = rr["value"], rr["confidence"]
        icon = "❓" if conf == "low" else "✅" if v >= 1.5 else "⚠️" if v < 0.8 else "➖"
        lines.append(f"   ⚖️ 盈虧比 1:{v:.2f} {icon}")
        if conf == "low" and rr.get("note"):
            lines.append(f"      ※{rr['note']}")

    pnl = risk.get("margin_pnl")
    if pnl is not None:
        if pnl > 15:
            lines.append(f"   ⚠️ 融資獲利{pnl:+.0f}%，賣壓重")
        elif pnl < -5:
            lines.append(f"   ✅ 融資套牢{pnl:+.0f}%，壓力釋放")

    rc = risk.get("margin", {}).get("recent_change")
    if rc is not None:
        if rc > 30:
            lines.append(f"   🔥 融資{rc:+.0f}% 慎入")
        elif rc < -10:
            lines.append(f"   ✅ 融資{rc:+.0f}% 籌碼沉澱")

    return lines


def build_message(env, picks, bb, golden, risks) -> str:
    now = tw_now()
    lines = [f"📊 {now.strftime('%m/%d')} 盤前情報", ""]

    lines.append(format_env(env))
    lines.append("")

    if golden:
        lines.append("💎 黃金組合（主力買+布林訊號）")
        lines.append("═" * 16)
        for g in golden[:4]:
            b, p = g["bb"], g["picks"]
            lines.append(f"{b['icon']} {g['name']} {g['code']}  {b['close']:.1f}")
            lines.append(f"   {g['source']}買超 連{p.get('streak', 0)}日｜{b['signal']}")
            if b["rank"] <= 1:
                lines.append(f"   量增{b['vol_ratio']:.1f}倍 紅K+{b['body']:.1f}%")
                lines.append(f"   {squeeze_text(b)}")
            else:
                lines.append(f"   縮口{b['squeeze_days']}日 分位{b['bw_rank']:.0f}")
            if g["code"] in risks:
                lines.extend(format_risk_brief(risks[g["code"]]))
        lines.append("")

    if bb:
        golden_codes = {g["code"] for g in golden}

        burst_rest = [r for r in bb.get("burst", []) if r["code"] not in golden_codes]
        if burst_rest:
            lines.append("🚀 今日噴出")
            lines.append("─" * 16)
            for r in burst_rest[:5]:
                lines.append(f"{r['icon']} {r['name']} {r['code']} {r['close']:.1f}")
                lines.append(f"   +{r['body']:.1f}% 量增{r['vol_ratio']:.1f}倍")
                lines.append(f"   {squeeze_text(r)}")
            lines.append("")

        if bb.get("diverge"):
            lines.append("⚠️ 量價背離（爆量不漲，避開）")
            for r in bb["diverge"][:4]:
                lines.append(f"  {r['name']} {r['code']} 量{r['vol_ratio']:.1f}倍 {r['body']:+.1f}%")
            lines.append("")

        squeeze_rest = [r for r in bb.get("squeeze", []) if r["code"] not in golden_codes]
        if squeeze_rest:
            lines.append("🔵 極度縮口（蓄勢待發）")
            lines.append("─" * 16)
            for r in squeeze_rest[:6]:
                lines.append(f"  {r['name']} {r['code']} {r['close']:.1f}")
                lines.append(f"   縮口{r['squeeze_days']}日 分位{r['bw_rank']:.0f}"
                             f"｜上軌{r['upper']:.1f} 下軌{r['lower']:.1f}")
            lines.append("")

    if picks:
        d = picks["date"]
        lines.append(f"📋 {d[4:6]}/{d[6:]} 主力動向")
        lines.append("═" * 16)

        golden_codes = {g["code"] for g in golden}

        both_rest = [x for x in picks.get("both", []) if x["code"] not in golden_codes]
        if both_rest:
            lines.append("🔥 雙主力同買")
            for x in both_rest:
                lines.extend(format_stock_line(x, show_trust=True))
                if x["code"] in risks:
                    lines.extend(format_risk_brief(risks[x["code"]]))
            lines.append("─" * 16)

        trust_rest = [x for x in picks.get("trust", []) if x["code"] not in golden_codes]
        if trust_rest:
            lines.append("🎯 投信買超")
            for x in trust_rest:
                lines.extend(format_stock_line(x))
                if x["code"] in risks:
                    lines.extend(format_risk_brief(risks[x["code"]]))
            lines.append("─" * 16)

        foreign_rest = [x for x in picks.get("foreign", []) if x["code"] not in golden_codes]
        if foreign_rest:
            lines.append("🏦 外資買超")
            for x in foreign_rest:
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
    force = os.environ.get("FORCE_RUN", "").lower() == "true"
    if not force and tw_now().weekday() >= 5:
        print("週末不執行（測試請用 morning_force）")
        return

    print("=== 1/5 環境判讀 ===")
    env = build_env()

    print("=== 2/5 主力動向 ===")
    picks = get_daily_picks()

    print("=== 3/5 布林掃描 ===")
    bb = scan_market()

    print("=== 4/5 黃金組合 ===")
    golden = find_golden(bb, picks)
    print(f"  找到 {len(golden)} 支")

    print("=== 5/5 風險地圖 ===")
    risks = {}
    for t in pick_risk_targets(picks or {}, golden):
        print(f"  分析 {t['name']} {t['code']}")
        r = build_risk_map(t["code"], t["name"])
        if r:
            risks[t["code"]] = r

    print("=== 組合訊息 ===")
    msg = build_message(env, picks, bb, golden, risks)
    print(msg)

    print("=== 推播 ===")
    send_line_message(msg)
    print("完成")


if __name__ == "__main__":
    main()
