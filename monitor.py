import os
import requests
import yfinance as yf
import datetime
from analyzer import get_top_picks

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]

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

    now = datetime.datetime.now().strftime("%H:%M")
    alerts = []

    for p in picks:
        ticker = p["ticker"]
        target = p["target"]
        stop = p["stop"]
        buy_price = p["price"]

        current = get_current_price(ticker)
        if current is None:
            continue

        change = (current - buy_price) / buy_price * 100
        print(f"{ticker} 現價:{current:.1f} 目標:{target} 停損:{stop} 漲跌:{change:.1f}%")

        if current >= target:
            alerts.append(
                f"🎯 達標提醒！\n"
                f"{ticker}\n"
                f"現價 {current:.1f} 已達目標價 {target}\n"
                f"建議考慮獲利了結 (+5%)\n"
                f"⚠️ 請自行判斷是否賣出"
            )
        elif current <= stop:
            alerts.append(
                f"🛑 停損提醒！\n"
                f"{ticker}\n"
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
