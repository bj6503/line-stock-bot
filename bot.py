import os
import requests
import anthropic
import datetime
from analyzer import get_top_picks

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

def get_stock_names() -> dict:
    """取得股票代號對應中文名稱"""
    names = {}
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        r = requests.get(url, timeout=10)
        for s in r.json():
            code = s.get("Code", "")
            name = s.get("Name", "")
            if code and name:
                names[code + ".TW"] = name
    except Exception as e:
        print(f"上市名稱取得失敗: {e}")
    try:
        url2 = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        r2 = requests.get(url2, timeout=10)
        for s in r2.json():
            code = s.get("SecuritiesCompanyCode", "")
            name = s.get("CompanyName", "")
            if code and name:
                names[code + ".TWO"] = name
    except Exception as e:
        print(f"上櫃名稱取得失敗: {e}")
    return names

def build_ai_summary(picks: list, names: dict) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    stock_info = ""
    for i, p in enumerate(picks, 1):
        name = names.get(p["ticker"], p["ticker"])
        stock_info += f"""
第{i}名 {name}({p['ticker']})
現價：{p['price']:.1f} 元
綜合評分：{p['score']}分
技術訊號：{', '.join(p['signals'])}
目標價：{p['target']} 元(+5%)
停損價：{p['stop']} 元(-3%)
建議股數：{p['shares']} 股
"""
    prompt = (
        "你是一位台灣股市短線分析師。以下是今日全市場掃描綜合評分前10名股票資料。\n"
        "請用繁體中文為每支股票寫一段30字以內的簡短操作建議，說明為何值得關注。\n"
        "結尾加上一句風險提示。不要使用markdown符號。\n\n"
        + stock_info
    )
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

def build_message(picks: list, summary: str, names: dict) -> str:
    date_str = datetime.date.today().strftime("%m/%d")
    lines = [f"📈 {date_str} 全市場掃描前10名", "─" * 20]
    for i, p in enumerate(picks, 1):
        name = names.get(p["ticker"], p["ticker"])
        ticker_short = p["ticker"].replace(".TW", "").replace("O", "")
        lines.append(
            f"#{i} {name} {ticker_short}\n"
            f"💵現價 {p['price']:.1f} 元\n"
            f"🎯目標 {p['target']} ｜🛑停損 {p['stop']}\n"
            f"📊{' '.join(p['signals'])}\n"
            f"💰建議買 {p['shares']} 股"
        )
        lines.append("─" * 20)
    lines.append("📝 AI分析：")
    lines.append(summary)
    lines.append("\n⚠️ 以上僅供參考，請自行判斷風險。")
    return "\n".join(lines)

def send_line_message(text: str):
    headers = {
        "Authorization": "Bearer " + LINE_TOKEN,
        "Content-Type": "application/json"
    }
    body = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        json=body, headers=headers
    )
    print("LINE 推播狀態: " + str(r.status_code))
    if r.status_code != 200:
        print(r.text)

def main():
    print("開始全市場掃描...")
    picks = get_top_picks(n=10)
    if not picks:
        print("今日無符合條件股票")
        send_line_message("今日全市場掃描無符合條件股票，請留意市場狀況。")
        return
    print("取得股票中文名稱...")
    names = get_stock_names()
    print("呼叫 Claude 生成分析...")
    summary = build_ai_summary(picks, names)
    print("組合訊息...")
    message = build_message(picks, summary, names)
    print("推播到 LINE...")
    send_line_message(message)
    print("完成！")

if __name__ == "__main__":
    main()
