import feedparser

POSITIVE = ["獲利", "成長", "創高", "突破", "漲", "訂單", "受惠", "強勁"]
NEGATIVE = ["虧損", "衰退", "下跌", "跌停", "裁員", "警告", "風險", "下修"]

def get_sentiment(keyword: str) -> dict:
    url = f"https://news.google.com/rss/search?q={keyword}+股票&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    feed = feedparser.parse(url)
    
    pos, neg = 0, 0
    headlines = []
    for entry in feed.entries[:8]:
        title = entry.title
        headlines.append(title)
        pos += sum(1 for w in POSITIVE if w in title)
        neg += sum(1 for w in NEGATIVE if w in title)
    
    sentiment = "正面" if pos > neg else ("負面" if neg > pos else "中性")
    return {
        "sentiment": sentiment,
        "pos": pos, "neg": neg,
        "headlines": headlines[:3]
    }
