import os
import requests
import datetime

from state import load_state
from watchlist import analyze_all, verdict, WATCHLIST

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

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
    for uid in [u.strip() for u in LINE_USER_ID.split(",") if u.strip()]:
        for i, chunk in enumerate(split_message(text)):
            body = {"to": uid, "messages": [{"type": "text", "text": chunk}]}
            r = requests.post("https://api.line.me/v2/bot/message/push",
                              json=body, headers=headers)
            print(f"推播 {uid[:8]}... ({i+1}) {r.status_code}")
            if r.status_code != 200:
                print(r.text)


def check_events(a: dict, morning: dict) -> list:
    """比對早盤設定的關卡，找出今日發生的事件"""
    events = []
    cur = a["close"]
    bb = a.get("bb", {})

    if not morning:
        return events

    res = morning.get("resistance")
    sup = morning.get("support")
    sup2 = morning.get("support2")
    up = morning.get("bb_upper")
    lo = morning.get("bb_lower")
    sig = morning.get("signal", "")

    # 縮口股表態
    if "縮口" in sig:
        if up and cur > up:
            events.append({"icon": "🟢", "type": "向上表態",
                           "text": f"站上上軌{up:.1f}，縮口轉多"})
        elif lo and cur < lo:
            events.append({"icon": "🔴", "type": "向下表態",
                           "text": f"跌破下軌{lo:.1f}，縮口轉空"})

    # 突破壓力
    if res and cur > res:
        events.append({"icon": "🟢", "type": "突破壓力",
                       "text": f"站上{res:.1f}"})

    # 跌破支撐
    if sup and cur < sup:
        nxt = f"，下一支撐{sup2:.1f}" if sup2 else ""
        events.append({"icon": "🔴", "type": "跌破支撐",
                       "text": f"失守{sup:.1f}{nxt}"})

    # 布林新訊號
    rank = bb.get("rank", 9)
    if rank == 0:
        events.append({"icon": "🚀", "type": "縮口噴出",
                       "text": f"量{bb['vol_ratio']:.1f}倍 K{bb['body']:+.1f}%"})
    elif rank == 2:
        events.append({"icon": "⚠️", "type": "量價背離",
                       "text": f"量{bb['vol_ratio']:.1f}倍但K{bb['body']:+.1f}%"})
    elif rank == 5:
        events.append({"icon": "🔻", "type": "跌破下軌",
                       "text": f"量{bb['vol_ratio']:.1f}倍"})

    # 大漲大跌
    if a["chg"] >= 5:
        events.append({"icon": "📈", "type": "強勢", "text": f"大漲{a['chg']:+.1f}%"})
    elif a["chg"] <= -5:
        events.append({"icon": "📉", "type": "重挫", "text": f"大跌{a['chg']:+.1f}%"})

    return events


def format_message(results: list, state: dict) -> str:
    now = tw_now()
    morning_map = {w["code"]: w for w in state.get("watch", [])}

    lines = [f"📕 {now.strftime('%m/%d')} 收盤檢討", ""]

    # 事件區
    all_events = []
    for a in results:
        evs = check_events(a, morning_map.get(a["code"]))
        if evs:
            all_events.append((a, evs))

    if all_events:
        lines.append("⚡ 今日關鍵事件")
        lines.append("═" * 16)
        for a, evs in all_events:
            lines.append(f"{a['name']} {a['code']}  {a['close']:.1f}（{a['chg']:+.1f}%）")
            for e in evs:
                lines.append(f"   {e['icon']} {e['type']}：{e['text']}")
        lines.append("")

    # 早盤判斷驗證
    if morning_map:
        lines.append("📝 早盤判斷驗證")
        lines.append("═" * 16)
        hits, misses = [], []
        for a in results:
            m = morning_map.get(a["code"])
            if not m:
                continue
            lvl = m.get("level")
            chg = a["chg"]
            if lvl == "watch":
                ok = chg > 0
            elif lvl == "avoid":
                ok = chg <= 0
            else:
                continue
            entry = f"  {a['name']}{a['code']} {chg:+.1f}%（早盤{m.get('source','')}）"
            (hits if ok else misses).append(entry)

        if hits:
            lines.append("✅ 符合預期")
            lines.extend(hits)
        if misses:
            lines.append("❌ 與預期相反")
            lines.extend(misses)
        lines.append("")

    # 明日分級
    lines.append("📋 明日分級")
    lines.append("═" * 16)
    groups = {"watch": [], "hold": [], "avoid": []}
    for a in results:
        groups[verdict(a)["level"]].append(a)

    for key, title in [("watch", "🟢 可留意"), ("hold", "🟡 觀望"), ("avoid", "🔴 避開")]:
        items = groups[key]
        if not items:
            continue
        lines.append(f"{title}（{len(items)}）")
        for a in items:
            v = verdict(a)
            rr = a.get("rr")
            rr_s = f"1:{rr:.1f}" if rr is not None else "—"
            sig = a.get("bb", {}).get("icon", "")
            lines.append(f"  {a['name']}{a['code']} {a['close']:.1f} "
                         f"{a['chg']:+.1f}% ⚖️{rr_s} {sig}")
            lines.append(f"    {v['reason']}")
        lines.append("")

    lines.append("⚠️ 僅供參考，請自行判斷風險")
    return "\n".join(lines)


def main():
    force = os.environ.get("FORCE_RUN", "").lower() == "true"
    if not force and tw_now().weekday() >= 5:
        print("週末不執行（測試請用 combo_force）")
        return

    print("=== 讀取早盤記錄 ===")
    state = load_state()
    print(f"  早盤記錄 {len(state.get('watch', []))} 支")

    print(f"=== 重新分析名單（{len(WATCHLIST)}支）===")
    results = analyze_all()

    if not results:
        print("無資料")
        return

    print("=== 組合訊息 ===")
    msg = format_message(results, state)
    print(msg)

    print("=== 推播 ===")
    send_line_message(msg)
    print("完成")


if __name__ == "__main__":
    main()
