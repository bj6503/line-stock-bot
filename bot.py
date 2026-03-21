import os
import requests
import anthropic
from analyzer import get_top_picks
from news import get_sentiment

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

STOCK_NAMES = {
    "2330.TW": "台積電",
    "2317.TW": "鴻海",
    "2454.TW": "聯發科",
    "2382.TW": "廣達",
    "3008.TW": "大立光",
}

def build_ai_summary(picks):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    stock_info = ""
    for p in picks:
        name = STOCK_NAMES.get(p["ticker"], p["ticker"])
        news = get_sentiment(name)
        stock_info += f"""
股票：{name} ({p['ticker']})
現價：{p['price']:.1f}
技術評分：{p['score']}/6
訊號：{', '.join(p['signals'])}
新聞情緒：{news['sentiment']}
近期標題：{news['headlines'][0] if news['headlines'] else '無'}
"""
    prompt = (
        "你是一位台灣股市分析師。請根據以下技術分析與新聞情緒，用繁體中文撰寫今日股票推薦摘要。\n"
        + stock_info
        + "\n要求：\n- 每檔股票一段，50字以內\n- 語氣專業但易懂\n- 結尾加上風險提示一句話\n- 不要使用markdown符號"
    )
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

def send_line_message(text):
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

def main():
    import datetime
    print("開始分析股票...")
    picks = get_top_picks(n=3)
    if not picks:
        print("無推薦股票")
        return
    print("呼叫 Claude 生成摘要...")
    summary = build_ai_summary(picks)
    date_str = datetime.date.today().strftime("%m/%d")
    message = "📈 " + date_str + " 今日股票推薦\n" + "─" * 20 + "\n" + summary
    print("推播到 LINE...")
    send_line_message(message)
    print("完成！")

if __name__ == "__main__":
    main()
