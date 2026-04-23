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
    """讀取預先篩選好的500元以上股票清單"""
    if os.path.exists("watchlist.json"):
        with open("watchlist.json", "r") as f:
            tickers = json.load(f)
        print(f"讀取watchlist.json，共 {len(tickers)} 支股票")
        return tickers
    else:
        print("watchlist.json不存在，使用預設清單")
        return [
            "2330.TW", "2454.TW", "2382.TW", "3008.TW",
            "2308.TW", "2395.TW", "3711.TW", "2379.TW",
            "2301.TW", "3034.TW", "2303.TW", "2344.TW"
        ]

def get_foreign_buy() -> set:
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

        k = ta.momentum.StochasticOscillator(high, low, close).stoch()
        d = ta.momentum.StochasticOscillator(high, low, close).stoch_signal()
        macd_obj = ta.trend.MACD(close)
        m
