import os
import requests
import anthropic
import datetime
import yfinance as yf
from analyzer import get_top_picks, get_momentum_picks
from news_picks import get_news_picks

LINE_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_USER_ID = os.environ["LINE_USER_ID"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

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

def get_current_price(ticker: str) -> float:
    try:
        df = yf.download(ticker, period="1d", interval="1m", progress=False)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception:
        return None

def check_alerts(picks, names) -> list:
    alerts = []
    for p in picks:
        ticker = p["ticker"]
        name = names.get(ticker, ticker)
        ticker_short = ticker.replace(".TW", "").replace("O", "")
        current = get_current_price(ticker)
        if current is None:
            continue
        if current >= p["target"]:
            alerts.append(f"🎯 {name} {ticker_short} 已達目標 {p['target']}")
        elif current <= p["stop"]:
            alerts.append(f"🛑 {name} {ticker_short} 跌破停損 {p['stop']}")
    return alerts

def build_message(top_picks, momentum_picks, news_data, names, alerts) -> str:
    now = datetime.datetime.now().strftime("%H:%M")
    date_str = datetime.date.today().strftime("%m/%d")
    lines = [f"📈 {date_str} {now} 盤中更新", "═" * 20]

    if alerts:
        lines.append("⏰ 達標/停損提醒")
        lines.append("─" * 20)
        lines.extend(alerts)
        lines.append("═" * 20)

    lines.append("🏆 技術綜合5強（穩健）")
    lines.append("─" * 20)
    for i, p in enumerate(top_picks, 1):
        name = names.get(p["ticker"], p["ticker"])
        ticker_short = p["ticker"].replace(".TW", "").replace("O", "")
        lines.append(
            f"#{i} {name} {ticker_short}\n"
            f"💵 現價 {p['price']:.1f}\n"
            f"🎯 目標 {p['target']} ｜🛑 停損 {p['stop']}\n"
            f"📊 {' '.join(p['signals'])}"
        )
    lines.append("═" * 20)

    lines.append("🚀 技術動能5強（追擊）")
    lines.append("─" * 20)
    for i, p in enumerate(momentum_picks, 1):
        name = names.get(p["ticker"], p["ticker"])
        ticker_short = p["ticker"].replace(".TW", "").replace("O", "")
        lines.append(
            f"#{i} {name} {ticker_short}\n"
            f"💵 現價 {p['price']:.1f}\n"
            f"🎯 目標 {p['target']} ｜🛑 停損 {p['stop']}\n"
            f"📊 {' '.join(p['signals'])}"
        )
    lines.append("═" * 20)

    lines.append("📰 新聞題材5強（事件驅動）")
    lines.append("─" * 20)
    news_picks = news_data.get("picks", [])
    if news_picks:
        for i, p in enumerate(news_picks, 1):
            stars = "⭐" * p.get("stars", 0)
            lines.append(
                f"#{i} {p.get('name', '')} {p.get('code', '')}\n"
                f"{stars}\n"
                f"💡 {p.get('reason', '')}"
            )
    else:
        lines.append("目前無明確題材股")
    lines.append("═" * 20)

    lines.append("⚠️ 以上僅供參考，請自行判斷風險。")
    return "\n".join(lines)

def send_line_message(text: str):
    headers = {
        "Authorization": "Bearer " + LINE_TOKEN,
        "Content-Type": "application/json"
    }
    user_ids = [uid.strip() for uid in LINE_USER_ID.split(",")]
    for uid in user_ids:
        body = {
            "to": uid,
            "messages": [{"type": "text", "text": text}]
        }
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            json=body, headers=headers
        )
        print(f"LINE 推播到 {uid[:8]}... 狀態: {r.status_code}")

def main():
    print("開始技術綜合掃描...")
    top_picks = get_top_picks(n=5)

    print("開始技術動能掃描...")
    momentum_picks = get_momentum_picks(n=5)

    print("開始新聞題材分析...")
    news_data = get_news_picks("盤中")

    if not top_picks and not momentum_picks and not news_data.get("picks"):
        print("無符合條件股票")
        return

    names = get_stock_names()

    print("檢查達標/停損...")
    all_picks = top_picks + momentum_picks
    alerts = check_alerts(all_picks, names)

    print("組合訊息...")
    message = build_message(top_picks, momentum_picks, news_data, names, alerts)

    print("推播到LINE...")
    send_line_message(message)
    print("完成！")

if __name__ == "__main__":
    main()
