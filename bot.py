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

def build_ai_summary(picks: list) -> str:
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
    
    prompt = f"""你是一位台灣股市分析師。請根據以下技術分析與新聞情緒，用繁體中文撰寫今日股票推薦摘要。

{stock_info}

要求：
- 每檔股票一段，50字以內
- 語氣專業但易懂
- 結尾加上風險提示一句話
- 不要使用markdown符號"""

    msg = client.messages.create(
        model="claude-son
