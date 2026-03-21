import yfinance as yf
import ta
import pandas as pd

WATCHLIST = ["2330.TW", "2317.TW", "2454.TW", "2382.TW", "3008.TW"]

def analyze_stock(ticker: str) -> dict:
    df = yf.download(ticker, period="3mo", interval="1d", progress=False)
    if df.empty or len(df) < 20:
        return None

    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    low = df["Low"].squeeze()

    # KD (Stochastic)
    k = ta.momentum.StochasticOscillator(high, low, close).stoch()
    d = ta.momentum.StochasticOscillator(high, low, close).stoch_signal()

    # MACD
    macd_obj = ta.trend.MACD(close)
    macd_val = macd_obj.macd().iloc[-1]
    macd_sig = macd_obj.macd_signal().iloc[-1]

    # RSI
    rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]

    k_val = k.iloc[-1]
    d_val = d.iloc[-1]

    # 計分邏輯
    score = 0
    signals = []

    if k_val < 20 and k_val > d_val:
        score += 2
        signals.append("KD黃金交叉(超賣區)")
    elif k_val > 80:
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
        "k": k_val, "d": d_val, "rsi": rsi,
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
