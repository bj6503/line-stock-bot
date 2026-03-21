import yfinance as yf
import ta
import pandas as pd
import requests
import time

BUDGET = 300000
POSITION_RATIO = 0.25

def get_twse_stocks() -> list:
    """取得全市場股票清單"""
    tickers = []
    try:
        # 上市股票
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        r = requests.get(url, timeout=10)
        data = r.json()
        for s in data:
            code = s.get("Code", "")
            if code.isdigit() and len(code) == 4:
                tickers.append(code + ".TW")
    except Exception as e:
        print(f"取得上市清單失敗: {e}")

    try:
        # 上櫃股票
        url2 = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        r2 = requests.get(url2, timeout=10)
        data2 = r2.json()
        for s in data2:
            code = s.get("SecuritiesCompanyCode", "")
            if code.isdigit() and len(code) == 4:
                tickers.append(code + ".TWO")
    except Exception as e:
        print(f"取得上櫃清單失敗: {e}")

    return list(set(tickers))

def get_foreign_buy() -> set:
    """取得外資買超股票清單"""
    foreign_buy = set()
    try:
        url = "https://openapi.twse.com.tw/v1/fund/TWT38U"
        r = requests.get(url, timeout=10)
        data = r.json()
        for s in data:
            buy = int(s.get("BuyShares", 0) or 0)
            sell = int(s.get("SellShares", 0) or 0)
            if buy > sell:
                foreign_buy.add(s.get("Code", "") + ".TW")
    except Exception as e:
        print(f"外資資料取得失敗: {e}")
    return foreign_buy

def analyze_stock(ticker: str, foreign_buy: set) -> dict:
    try:
        df = yf.download(ticker, period="6mo", interval="1d", progress=False, timeout=10)
        if df.empty or len(df) < 30:
            return None

        close = df["Close"].squeeze()
        high = df["High"].squeeze()
        low = df["Low"].squeeze()
        volume = df["Volume"].squeeze()

        # 技術指標
        k = ta.momentum.StochasticOscillator(high, low, close).stoch()
        d = ta.momentum.StochasticOscillator(high, low, close).stoch_signal()
        macd_obj = ta.trend.MACD(close)
        macd_val = macd_obj.macd().iloc[-1]
        macd_sig = macd_obj.macd_signal().iloc[-1]
        rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]
        k_val = k.iloc[-1]
        d_val = d.iloc[-1]

        price = float(close.iloc[-1])
        score = 0
        signals = []

        # KD
        if k_val < 20 and k_val > d_val:
            score += 2
            signals.append("KD黃金交叉")
        elif k_val > 80:
            score -= 1

        # MACD
        if macd_val > macd_sig and macd_val < 0:
            score += 2
            signals.append("MACD黃金交叉")
        elif macd_val > macd_sig:
            score += 1
            signals.append("MACD多頭")

        # RSI
        if rsi < 35:
            score += 2
            signals.append(f"RSI超賣({rsi:.0f})")
        elif rsi > 70:
            score -= 1

        # 成交量爆增（今日量 > 5日均量 * 2）
        vol_ma5 = volume.iloc[-6:-1].mean()
        vol_today = float(volume.iloc[-1])
        if vol_today > vol_ma5 * 2:
            score += 2
            signals.append("成交量爆增")

        # 突破近6個月高點
        high_6m = float(high.iloc[:-1].max())
        if price >= high_6m * 0.99:
            score += 3
            signals.append("突破6月高點")

        # 外資買超
        if ticker in foreign_buy:
            score += 2
            signals.append("外資買超")

        if score <= 0:
            return None

        target = round(price * 1.05, 1)
        stop = round(price * 0.97, 1)
        shares = int(BUDGET * POSITION_RATIO / price / 1000) * 1000

        return {
            "ticker": ticker,
            "price": price,
            "score": score,
            "signals": signals,
            "rsi": rsi,
            "target": target,
            "stop": stop,
            "shares": shares,
        }
    except Exception:
        return None

def get_top_picks(n=10) -> list:
    print("取得全市場股票清單...")
    tickers = get_twse_stocks()
    print(f"共 {len(tickers)} 支股票，開始掃描...")

    print("取得外資買超資料...")
    foreign_buy = get_foreign_buy()

    results = []
    for i, t in enumerate(tickers):
        if i % 100 == 0:
            print(f"進度: {i}/{len(tickers)}")
        r = analyze_stock(t, foreign_buy)
        if r:
            results.append(r)
        time.sleep(0.1)  # 避免請求過快被封鎖

    results.sort(key=lambda x: x["score"], reverse=True)
    print(f"掃描完成，共找到 {len(results)} 支符合條件")
    return results[:n]
