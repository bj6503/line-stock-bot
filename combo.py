import os
import requests
import anthropic
import datetime
import yfinance as yf
from analyzer import get_top_picks, get_momentum_picks

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

def build_ai_summary(top_picks, momentum_picks, names) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    info = "【綜合評分5強】\n"
    for i, p in enumerate(top_picks, 1):
        name = names.get(p["ticker"], p["ticker"])
        info += f"{i}. {name}({p['ticker']}) 現價{p['price']:.0f} 評分{p['score']} {','.join(p['signals'])}\n"
    info += "\n【強勢動能5強】\n"
    for i, p in enumerate(momentum_picks, 1):
        name = names.get(p["ticker"], p["ticker"])
        info += f"{i}. {name}({p['ticker']}) 現價{p['price']:.0f} 評分{p['score']} {','.join(p['signals'])}\n"
    prompt = (
        "你是一位台灣股市短線分析師。以下是當前掃描結果。\n"
        "請用繁體中文為每支股票寫一段25字以內的操作建議。\n"
        "結尾加上一句風險提示。不要使用markdown符號。\n\n"
        + info
    )
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

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

def build_message(top_picks, momentum_picks, summary, names, alerts) -> str:
    now = datetime.datetime.now().strftime("%H:%M")
    date_str = datetime.date.today().strftime("%m/%d")
    lines = [f"📈 {date_str} {now} 推薦更新", "═" * 20]

    if alerts:
        lines.append("⏰ 達標/停損提醒")
        lines.append("─" * 20)
        lines.extend(alerts)
        lines.append("═" * 20)

    lines.append("🏆 綜合評分5強（穩健）")
    lines.append("─" * 20)
    for i, p in enumerate(top_picks, 1):
        name = names.get(p["ticker"], p["ticker"])
        ticker_short = p["ticker"].replace(".TW", "").replace("O", "")
        lines.append(
            f"#{i} {name} {ticker_short}\n"
            f"💵 現價 {p['price']:.1f}\n"
            f"🎯 目標 {p['target']} ｜🛑 停損 {p['stop']}\n"
            f"📊 {' '.join(p['signals'])}\n"
            f"💰 建議買 {p['shares']} 股(零股)"
        )
    lines.append("═" * 20)

    lines.append("🚀 強勢動能5強（追擊）")
    lines.append("─" * 20)
    for i, p in enumerate(momentum_picks, 1):
        name = names.get(p["ticker"], p["ticker"])
        ticker_short = p["ticker"].replace(".TW", "").replace("O", "")
        lines.append(
            f"#{i} {name} {ticker_short}\n"
            f"💵 現價 {p['price']:.1f}\n"
            f"🎯 目標 {p['target']} ｜🛑 停損 {p['stop']}\n"
            f"📊 {' '.join(p['signals'])}\n"
            f"💰 建議買 {p['shares']} 股(零股)"
        )
    lines.append("═" * 20)

    lines.append("📝 AI分析：")
    lines.append(summary)
    lines.append("\n⚠️ 以上僅供參考，請自行判斷風險。")
    return "\n".join(lines)

def main():
    print("開始掃描...")
    top_picks = get_top_picks(n=5)
    momentum_picks = get_momentum_picks(n=5)

    if not top_picks and not momentum_picks:
        print("無符合條件股票")
        return

    names = get_stock_names()

    print("檢查達標/停損...")
    all_picks = top_picks + momentum_picks
    alerts = check_alerts(all_picks, names)

    print("生成AI分析...")
    summary = build_ai_summary(top_picks, momentum_picks, names)

    print("推播到LINE...")
    message = build_message(top_picks, momentum_picks, summary, names, alerts)
    send_line_message(message)
    print("完成！")

if __name__ == "__main__":
    main()
