import os
import requests
import datetime

from watchlist import analyze_all, format_report, verdict, WATCHLIST
from state import save_state

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
    chunks = split_message(text)
    for uid in [u.strip() for u in LINE_USER_ID.split(",") if u.strip()]:
        for i, chunk in enumerate(chunks):
            body = {"to": uid, "messages": [{"type": "text", "text": chunk}]}
            r = requests.post("https://api.line.me/v2/bot/message/push",
                              json=body, headers=headers)
            print(f"推播 {uid[:8]}... ({i+1}/{len(chunks)}) {r.status_code}")
            if r.status_code != 200:
                print(r.text)


def build_watch_entries(results: list) -> list:
    entries = []
    for a in results:
        v = verdict(a)
        res = a["ladder"]["resistance"]
        sup = a["ladder"]["support"]
        bb = a.get("bb", {})
        entries.append({
            "code": a["code"],
            "name": a["name"],
            "source": v["text"],
            "level": v["level"],
            "ref_price": a["close"],
            "data_date": a.get("data_date", ""),
            "signal": bb.get("signal", "") or "中性",
            "resistance": res[0]["price"] if res else None,
            "support": sup[0]["price"] if sup else None,
            "support2": sup[1]["price"] if len(sup) >= 2 else None,
            "bb_upper": bb.get("upper"),
            "bb_lower": bb.get("lower"),
            "rr": a.get("rr"),
        })
    return entries


def build_message(results: list) -> str:
    now = tw_now()
    data_date = results[0].get("data_date", "") if results else ""
    dd = f"（資料日 {data_date[5:]}）" if data_date else ""
    lines = [f"📊 {now.strftime('%m/%d')} 盤前分析{dd}", ""]
    lines.append(format_report(results))
    lines.append("⚠️ 僅供參考，請自行判斷風險")
    return "\n".join(lines)


def main():
    force = os.environ.get("FORCE_RUN", "").lower() == "true"
    if not force and tw_now().weekday() >= 5:
        print("週末不執行（測試請用 morning_force）")
        return

    print(f"=== 分析觀察名單（{len(WATCHLIST)}支）===")
    results = analyze_all()

    if not results:
        print("無資料")
        send_line_message("⚠️ 今日資料取得失敗，請檢查系統")
        return

    print("=== 儲存追蹤清單 ===")
    save_state({"watch": build_watch_entries(results)})
    print(f"  記錄 {len(results)} 支")

    print("=== 組合訊息 ===")
    msg = build_message(results)
    print(msg)

    print("=== 推播 ===")
    send_line_message(msg)
    print("完成")


if __name__ == "__main__":
    main()
