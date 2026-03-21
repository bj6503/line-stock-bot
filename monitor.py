import os
import requests
import yfinance as yf
import datetime
from analyzer import get_top_picks

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

def get_stock_names() -> dict:
    names = {}
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        r = requests.get(url, timeout=10)
        for s in r.json():
            code = s.get("Code", "")
            name = s.get("Name", "")
            if code and name:
                names[code + ".TW"] = name
    except Exception as e:
        print(f"上市名稱取得失敗: {e}")
    try:
        url2 = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        r2 = requests.get(url2, timeout=10)
        for s in r2.json():
            code = s.get("SecuritiesCompanyCode", "")
            name = s.get("CompanyName", "")
            if code and name:
                names[code + ".TWO"] = name
    except Exception as e:
        print(f"上櫃名稱取得失敗: {e}")
    return names

def send_line_message(text: str):
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

def get_current_price(ticker: str) -> float:
    try:
        df = yf.download(ticker, period="1d", interval="1m", progress=False)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None

def monitor():
    print("開始盤中監控...")
    picks = get_top_picks(n=10)
    if not picks:
        print("無監控標的")
        return

    names = get_stock_names()
    now = datetime.datetime.now().strftime("%H:%M")
    alerts = []

    for p in picks:
        ticker = p["ticker"]
        target = p["target"]
        stop = p["stop"]
        buy_price = p["price"]
        name = names.get(ticker, ticker)
        ticker_short = ticker.replace(".TW", "").replace("O", "")

        current = get_current_price(ticker)
        if current is None:
            continue

        change = (current - buy_price) / buy_price * 100
        print(f"{name} {ticker_short} 現價:{current:.1f} 漲跌:{change:.1f}%")

        if current >= target:
            alerts.append(
                f"🎯 達標提醒！\n"
                f"{name} {ticker_short}\n"
                f"現價 {current:.1f} 已達目標價 {target}\n"
                f"建議考慮獲利了結 (+5%)\n"
                f"⚠️ 請自行判斷是否賣出"
            )
        elif current <= stop:
            alerts.append(
                f"🛑 停損提醒！\n"
                f"{name} {ticker_short}\n"
                f"現價 {current:.1f} 已跌破停損價 {stop}\n"
                f"建議考慮停損出場 (-3%)\n"
                f"⚠️ 請自行判斷是否賣出"
            )

    if alerts:
        message = f"⏰ {now} 盤中提醒\n" + "─" * 20 + "\n"
        message += "\n─────\n".join(alerts)
        send_line_message(message)
        print(f"發送 {len(alerts)} 則提醒")
    else:
        print("無達標或停損標的，不發送通知")

if __name__ == "__main__":
    monitor()
