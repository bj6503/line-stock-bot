import os
import requests
import anthropic
import feedparser
import yfinance as yf
import json

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

def get_us_market() -> str:
    """取得美股三大指數表現"""
    indices = {
        "^SOX": "費城半導體",
        "^DJI": "道瓊工業",
        "^IXIC": "納斯達克",
        "^GSPC": "標普500"
    }
    result = "【美股昨夜表現】\n"
    for symbol, name in indices.items():
        try:
            df = yf.download(symbol, period="2d", interval="1d", progress=False)
            if len(df) >= 2:
                close = df["Close"].squeeze()
                change = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
                result += f"{name}: {change:+.2f}%\n"
        except Exception:
            continue
    return result

def get_taiwan_news() -> str:
    """取得台股相關新聞標題"""
    feeds = [
        "https://news.google.com/rss/search?q=台股+類股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
        "https://news.google.com/rss/search?q=台積電+半導體+訂單&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
        "https://news.google.com/rss/search?q=外資+買超+台股&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",
    ]
    result = "【台股新聞題材】\n"
    seen = set()
    for url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:6]:
                title = entry.title
                if title not in seen:
                    seen.add(title)
                    result += f"- {title}\n"
        except Exception:
            continue
    return result

def get_news_picks(time_range: str = "盤前") -> dict:
    """用AI分析新聞，推薦受惠股票"""
    print("收集美股表現...")
    us_market = get_us_market()
    print("收集台股新聞...")
    tw_news = get_taiwan_news()

    print("呼叫Claude分析...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    prompt = f"""你是專業台股分析師。現在是{time_range}時段，以下是最新市場資訊：

{us_market}

{tw_news}

請根據以上美股表現、新聞題材、資金流向，分析並推薦5支「最可能因這些事件受惠而上漲」的台股。

要求：
1. 只推薦台股上市櫃股票，給出正確股票代號
2. 每支股票給「強勢星等」：1到5（5最強）
3. 說明推薦理由（與哪則新聞/事件相關），30字以內
4. 只推薦你有信心的，寧缺勿濫

請用以下JSON格式回覆，不要有其他文字：
{{
  "picks": [
    {{"code": "2330", "name": "台積電", "stars": 5, "reason": "費半大漲，台積電ADR領漲"}}
  ]
}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    text = msg.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()

    try:
        data = json.loads(text)
        return data
    except Exception as e:
        print(f"JSON解析失敗: {e}")
        print(f"原始回覆: {text}")
        return {"picks": []}

if __name__ == "__main__":
    result = get_news_picks("盤前")
    for p in result.get("picks", []):
        print(f"{'⭐' * p['stars']} {p['name']}({p['code']}) - {p['reason']}")
