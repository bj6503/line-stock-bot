import os
import requests
import datetime
import yfinance as yf

TOKEN = os.environ.get("FINMIND_TOKEN", "")
API_URL = "https://api.finmindtrade.com/api/v4/data"


def fm_query(dataset: str, data_id: str = "", start_date: str = "") -> list:
    params = {"dataset": dataset, "token": TOKEN}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    try:
        j = requests.get(API_URL, params=params, timeout=15).json()
        if j.get("status") != 200:
            return []
        return j.get("data", [])
    except Exception:
        return []


def get_us_indices() -> dict:
    """美股四大指數 + 台股ADR"""
    targets = {
        "^SOX": "費半",
        "^IXIC": "納斯達克",
        "^DJI": "道瓊",
        "^GSPC": "標普500",
        "TSM": "台積電ADR",
        "UMC": "聯電ADR",
    }
    out = {}
    for sym, name in targets.items():
        try:
            df = yf.download(sym, period="5d", interval="1d", progress=False)
            if len(df) < 2:
                continue
            close = df["Close"].squeeze()
            chg = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100
            out[name] = {"value": float(close.iloc[-1]), "change": float(chg)}
        except Exception:
            continue
    return out


def get_fx() -> dict:
    """美元台幣匯率與趨勢"""
    start = (datetime.date.today() - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
    data = fm_query("TaiwanExchangeRate", "USD", start)
    if len(data) < 2:
        return {}

    rates = [(d["date"], float(d.get("spot_sell", 0) or 0)) for d in data if d.get("spot_sell")]
    if len(rates) < 2:
        return {}

    latest = rates[-1][1]
    prev = rates[-2][1]
    week_ago = rates[-6][1] if len(rates) >= 6 else rates[0][1]

    # 台幣升值 = 匯率數字下降
    day_chg = latest - prev
    week_chg = latest - week_ago

    # 連續升值天數
    streak = 0
    for i in range(len(rates) - 1, 0, -1):
        if rates[i][1] < rates[i - 1][1]:
            streak += 1
        else:
            break

    if week_chg < -0.15:
        signal = "台幣走強，外資錢在進"
        icon = "🟢"
    elif week_chg > 0.15:
        signal = "台幣走弱，外資可能撤"
        icon = "🔴"
    else:
        signal = "匯率持平"
        icon = "➖"

    return {
        "rate": latest,
        "day_change": day_chg,
        "week_change": week_chg,
        "up_streak": streak,
        "signal": signal,
        "icon": icon,
    }


def get_futures_oi() -> dict:
    """外資台指期未平倉淨部位"""
    start = (datetime.date.today() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")
    data = fm_query("TaiwanFuturesInstitutionalInvestors", "TX", start)
    if not data:
        return {}

    by_date = {}
    for d in data:
        if d.get("institutional_investors") != "外資":
            continue
        date = d.get("date")
        oi_long = float(d.get("open_interest_balance_long_volume", 0) or 0)
        oi_short = float(d.get("open_interest_balance_short_volume", 0) or 0)
        by_date[date] = oi_long - oi_short

    if not by_date:
        return {}

    dates = sorted(by_date.keys())
    latest = by_date[dates[-1]]
    prev = by_date[dates[-2]] if len(dates) >= 2 else latest
    change = latest - prev

    if latest > 5000:
        stance = "外資期貨偏多"
        icon = "🟢"
    elif latest < -5000:
        stance = "外資期貨偏空"
        icon = "🔴"
    else:
        stance = "外資期貨中性"
        icon = "➖"

    return {
        "net_oi": latest,
        "change": change,
        "stance": stance,
        "icon": icon,
    }


def build_env() -> dict:
    print("收集美股與ADR...")
    us = get_us_indices()
    print("收集匯率...")
    fx = get_fx()
    print("收集外資期貨部位...")
    fut = get_futures_oi()

    # 綜合方向判斷
    score = 0
    if us.get("費半", {}).get("change", 0) > 1:
        score += 2
    elif us.get("費半", {}).get("change", 0) < -1:
        score -= 2
    if us.get("台積電ADR", {}).get("change", 0) > 1:
        score += 2
    elif us.get("台積電ADR", {}).get("change", 0) < -1:
        score -= 2
    if fx.get("week_change", 0) < -0.15:
        score += 1
    elif fx.get("week_change", 0) > 0.15:
        score -= 1
    if fut.get("net_oi", 0) > 5000:
        score += 1
    elif fut.get("net_oi", 0) < -5000:
        score -= 1

    if score >= 3:
        verdict, vicon = "偏多，可積極", "🟢"
    elif score <= -3:
        verdict, vicon = "偏空，宜保守", "🔴"
    else:
        verdict, vicon = "中性，選股為主", "🟡"

    return {
        "us": us, "fx": fx, "futures": fut,
        "score": score, "verdict": verdict, "verdict_icon": vicon,
    }


def format_env(env: dict) -> str:
    if not env:
        return ""

    lines = ["🌏 今日環境判讀", "═" * 16]

    us = env.get("us", {})
    for name in ["費半", "納斯達克", "道瓊"]:
        if name in us:
            v = us[name]
            icon = "🟢" if v["change"] > 0 else "🔴" if v["change"] < 0 else "➖"
            lines.append(f"{icon} {name} {v['change']:+.2f}%")

    for name in ["台積電ADR", "聯電ADR"]:
        if name in us:
            v = us[name]
            icon = "🟢" if v["change"] > 0 else "🔴"
            lines.append(f"{icon} {name} {v['change']:+.2f}%")

    fx = env.get("fx", {})
    if fx:
        lines.append("─" * 16)
        lines.append(f"{fx['icon']} 匯率 {fx['rate']:.3f}（週{fx['week_change']:+.3f}）")
        lines.append(f"   {fx['signal']}")

    fut = env.get("futures", {})
    if fut:
        lines.append(f"{fut['icon']} 外資期貨淨{fut['net_oi']:+.0f}口")
        lines.append(f"   {fut['stance']}")

    lines.append("═" * 16)
    lines.append(f"{env['verdict_icon']} 綜合判讀：{env['verdict']}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_env(build_env()))
