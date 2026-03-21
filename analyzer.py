import yfinance as yf
import pandas_ta as ta
import pandas as pd

WATCHLIST = ["2330.TW", "2317.TW", "2454.TW", "2382.TW", "3008.TW"]

def analyze_stock(ticker: str) -> dict:
    df = yf.download(ticker, period="3mo", interval="1d", progress=False)
    if df.empty or len(df) < 20:
        return None

    close = df["Close"].squeeze()

    # KD (Stochastic)
    stoch = ta.stoch(df["High"].squeeze(), df["Low"].squeeze(), close)
    k = stoch["STOCHk_14_3_3"].iloc[-1]
    d = stoch["STOCHd_14_3_3"].iloc[-1]

    # MACD
    macd = ta.macd(close)
    macd_val = macd["MACD_12_26_9"].iloc[-1]
    macd_sig = macd["MACDs_12_26_9"].iloc[-1]

    # RSI
    rsi = ta.rsi(close).iloc[-1]

    # 計分邏輯
    score = 0
    signals = []

    if k < 20 and k > d:
        score += 2
        signals.append("KD黃金交叉(超賣區)")
    elif k > 80:
        score -= 1
        signals.append("KD超買")

    if macd_val > macd_sig and macd_val < 0:
        score += 2
        signals.append("MACD黃金交叉(零軸下)")
    elif macd_val > macd_sig:
        score += 1
        signals.append("MACD多頭")

    if rsi < 35:
        score += 2
        signals.append(f"RSI超賣({rsi:.0f})")
    elif rsi > 70:
        score -= 1
        signals.append(f"RSI超買({rsi:.0f})")

    return {
        "ticker": ticker,
        "price": float(close.iloc[-1]),
        "score": score,
        "signals": signals,
        "k": k, "d": d, "rsi": rsi,
        "macd": macd_val
    }

def get_top_picks(n=3) -> list:
    results = []
    for t in WATCHLIST:
        r = analyze_stock(t)
        if r:
            results.append(r)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:n]
