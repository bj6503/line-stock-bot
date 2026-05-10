import yfinance as yf
import ta
import pandas as pd
import requests
import time
import json
import os

BUDGET = 300000
POSITION_RATIO = 0.25

def get_watchlist() -> list:
    if os.path.exists("watchlist.json"):
        with open("watchlist.json", "r") as f:
            tickers = json.load(f)
        print(f"讀取watchlist.json，共 {len(tickers)} 支股票")
        return tickers
    else:
        return [
            "2330.TW", "2454.TW", "2382.TW", "3008.TW",
            "2308.TW", "2395.TW", "3711.TW", "2379.TW",
        ]

def get_foreign_buy() -> set:
    foreign_buy = set()
    try:
        url = "https://openapi.twse.com.tw/v1/fund/TWT38U"
        r = requests.get(url, timeout=10)
        for s in r.json():
            buy = int(s.get("BuyShares", 0) or 0)
            sell = int(s.get("SellShares", 0) or 0)
            if buy > sell:
                foreign_buy.add(s.get("Code", "") + ".TW")
    except Exception as e:
        print(f"外資資料取得失敗: {e}")
    return foreign_buy

def analyze_stock(ticker: str, foreign_buy: set) -> dict:
    """綜合評分分析"""
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False, timeout=10)
        if df.empty or len(df) < 30:
            return None

        close = df["Close"].squeeze()
        high = df["High"].squeeze()
        low = df["Low"].squeeze()
        volume = df["Volume"].squeeze()

        k = ta.momentum.StochasticOscillator(high, low, close).stoch()
        d = ta.momentum.StochasticOscillator(high, low, close).stoch_signal()
        macd_obj = ta.trend.MACD(close)
        macd_val = macd_obj.macd().iloc[-1]
        macd_sig = macd_obj.macd_signal().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]

        k_val = k.iloc[-1]
        d_val = d.iloc[-1]
        price = float(close.iloc[-1])

        if price < 500:
            return None

        score = 0
        signals = []

        if k_val < 20 and k_val > d_val:
            score += 2
            signals.append("KD黃金交叉")
        elif k_val > 80:
            score -= 1

        if macd_val > macd_sig and macd_val < 0:
            score += 2
            signals.append("MACD黃金交叉")
        elif macd_val > macd_sig:
            score += 1
            signals.append("MACD多頭")

        if rsi < 35:
            score += 2
            signals.append(f"RSI超賣({rsi:.0f})")
        elif rsi > 70:
            score -= 1

        vol_ma5 = volume.iloc[-6:-1].mean()
        vol_today = float(volume.iloc[-1])
        if vol_today > vol_ma5 * 2:
            score += 2
            signals.append("成交量爆增")

        high_6m = float(high.iloc[:-1].max())
        if price >= high_6m * 0.99:
            score += 3
            signals.append("突破6月高點")

        if ticker in foreign_buy:
            score += 2
            signals.append("外資買超")

        if score <= 0:
            return None

        target = round(price * 1.05, 1)
        stop = round(price * 0.97, 1)
        shares = int(BUDGET * POSITION_RATIO / price)

        return {
            "ticker": ticker, "price": price, "score": score,
            "signals": signals, "rsi": rsi,
            "target": target, "stop": stop, "shares": shares,
        }
    except Exception:
        return None

def analyze_momentum(ticker: str, foreign_buy: set) -> dict:
    """強勢動能分析（找近期可能大漲的股票）"""
    try:
        df = yf.download(ticker, period="3mo", interval="1d", progress=False, timeout=10)
        if df.empty or len(df) < 20:
            return None

        close = df["Close"].squeeze()
        high = df["High"].squeeze()
        volume = df["Volume"].squeeze()

        price = float(close.iloc[-1])
        if price < 500:
            return None

        score = 0
        signals = []

        # 1. 連續上漲天數
        up_days = 0
        for i in range(-1, -6, -1):
            if close.iloc[i] > close.iloc[i-1]:
                up_days += 1
            else:
                break
        if up_days >= 3:
            score += 3
            signals.append(f"連{up_days}日上漲")

        # 2. 成交量持續放大
        vol_ma5 = volume.iloc[-6:-1].mean()
        vol_today = float(volume.iloc[-1])
        if vol_today > vol_ma5 * 1.5:
            score += 2
            signals.append("量增")
        if vol_today > vol_ma5 * 3:
            score += 2
            signals.append("爆量")

        # 3. 站上5/20日均線
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        if price > ma5 > ma20:
            score += 2
            signals.append("均線多頭")

        # 4. 近5日漲幅
        change_5d = (price - close.iloc[-6]) / close.iloc[-6] * 100
        if change_5d > 5:
            score += 2
            signals.append(f"5日漲{change_5d:.0f}%")

        # 5. 突破近20日高點
        high_20 = float(high.iloc[-21:-1].max())
        if price >= high_20:
            score += 3
            signals.append("突破20日高")

        # 6. 外資買超
        if ticker in foreign_buy:
            score += 2
            signals.append("外資買超")

        # 7. 今日漲幅 > 3%
        change_today = (price - close.iloc[-2]) / close.iloc[-2] * 100
        if change_today > 3:
            score += 2
            signals.append(f"今日漲{change_today:.1f}%")

        if score < 5:
            return None

        target = round(price * 1.05, 1)
        stop = round(price * 0.97, 1)
        shares = int(BUDGET * POSITION_RATIO / price)

        return {
            "ticker": ticker, "price": price, "score": score,
            "signals": signals,
            "target": target, "stop": stop, "shares": shares,
            "change_today": change_today,
        }
    except Exception:
        return None

def get_top_picks(n=5) -> list:
    tickers = get_watchlist()
    print(f"開始綜合評分掃描 {len(tickers)} 支股票...")
    foreign_buy = get_foreign_buy()
    results = []
    for i, t in enumerate(tickers):
        if i % 50 == 0:
            print(f"進度: {i}/{len(tickers)}")
        r = analyze_stock(t, foreign_buy)
        if r:
            results.append(r)
        time.sleep(0.05)
    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"綜合評分完成，找到 {len(results)} 支")
    return results[:n]

def get_momentum_picks(n=5) -> list:
    tickers = get_watchlist()
    print(f"開始強勢動能掃描 {len(tickers)} 支股票...")
    foreign_buy = get_foreign_buy()
    results = []
    for i, t in enumerate(tickers):
        if i % 50 == 0:
            print(f"進度: {i}/{len(tickers)}")
        r = analyze_momentum(t, foreign_buy)
        if r:
            results.append(r)
        time.sleep(0.05)
    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"強勢動能完成，找到 {len(results)} 支")
    return results[:n]
