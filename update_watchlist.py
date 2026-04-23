import requests
import json
import yfinance as yf
import time

def get_all_stocks() -> list:
    tickers = []
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        r = requests.get(url, timeout=10)
        for s in r.json():
            code = s.get("Code", "")
            if code.isdigit() and len(code) == 4:
                tickers.append(code + ".TW")
    except Exception as e:
        print(f"上市清單失敗: {e}")
    try:
        url2 = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        r2 = requests.get(url2, timeout=10)
        for s in r2.json():
            code = s.get("SecuritiesCompanyCode", "")
            if code.isdigit() and len(code) == 4:
                tickers.append(code + ".TWO")
    except Exception as e:
        print(f"上櫃清單失敗: {e}")
    return list(set(tickers))

def filter_above_500(tickers: list) -> list:
    result = []
    print(f"開始篩選500元以上股票，共 {len(tickers)} 支...")
    for i, ticker in enumerate(tickers):
        if i % 100 == 0:
            print(f"進度: {i}/{len(tickers)}")
        try:
            df = yf.download(ticker, period="5d", interval="1d", progress=False, timeout=8)
            if df.empty:
                continue
            price = float(df["Close"].squeeze().iloc[-1])
            if price >= 500:
                result.append(ticker)
                print(f"✓ {ticker} 現價 {price:.0f} 元")
        except Exception:
            continue
        time.sleep(0.05)
    return result

def main():
    print("取得全市場股票清單...")
    all_tickers = get_all_stocks()

    print("篩選500元以上股票...")
    watchlist = filter_above_500(all_tickers)

    print(f"\n共找到 {len(watchlist)} 支500元以上股票")

    with open("watchlist.json", "w") as f:
        json.dump(watchlist, f)
    print("已儲存到 watchlist.json")

if __name__ == "__main__":
    main()
