import os
import requests
import datetime
import time
import yfinance as yf

TOKEN = os.environ.get("FINMIND_TOKEN", "")
API_URL = "https://api.finmindtrade.com/api/v4/data"

MAX_RETRY = 3


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


def fetch_quote(symbol: str) -> dict:
    """抓單一標的漲跌，含重試"""
    for attempt in range(MAX_RETRY):
        try:
            df = yf.download(symbol, period="7d", interval="1d",
                             progress=False, auto_adjust=False)
            if df is None or len(df) < 2:
                time.sleep(1.5)
                continue
            close = df["Close"].squeeze()
            closes = [float(c) for c in close.tolist() if c == c]
            if len(closes) < 2:
                time.sleep(1.5)
                continue
            chg = (closes[-1] - closes[-2]) / closes[-2] * 100
            return {"value": closes[-1], "change": chg}
        except Exception:
            time.sleep(1.5)
    return {}


def fetch_with_fallback(primary: str, fallback: str, name: str) -> dict:
    """主要來源失敗時改用備援標的"""
    q = fetch_quote(primary)
    if q:
        return q
    print(f"  {name}({primary}) 失敗，改用備援 {fallback}")
    q = fetch_quote(fallback)
    if q:
        q["fallback"] = True
    return q


def get_us_indices() -> dict:
    out = {}
    # 費半：主 ^SOX，備援 SOXX（追蹤費半的ETF）
    sox = fetch_with_fallback("^SOX", "SOXX", "費半")
    if sox:
        out["費半"] = sox
    else:
        out["費半"] = {"missing": True}

    others = [
        ("^IXIC", "納斯達克", "QQQ"),
        ("^DJI", "道瓊", "DIA"),
        ("^GSPC", "標普500", "SPY"),
    ]
    for sym, name, fb in others:
        q = fetch_with_fallback(sym, fb, name)
        if q:
            out[name] = q
        else:
            out[name] = {"missing": True}

    for sym, name in [("TSM", "台積電ADR"), ("UMC", "聯電ADR")]:
        q = fetch_quote(sym)
        if q:
            out[name] = q
        else:
            out[name] = {"missing": True}

    return out


def get_fx() -> dict:
    start = (datetime.date.today() - datetime.timedelta(days=25)).strftime("%Y-%m-%d")
    data = fm_query("TaiwanExchangeRate", "USD", start)
    rates = [(d["date"], float(d.get("spot_sell", 0) or 0))
             for d in data if d.get("spot_sell")]
    if len(rates) < 2:
        return {}

    latest = rates[-1][1]
    prev = rates[-2][1]
    week_ago = rates[-6][1] if len(rates) >= 6 else rates[0][1]

    day_chg = latest - prev
    week_chg = latest - week_ago

    streak = 0
    for i in range(len(rates) - 1, 0, -1):
        if rates[i][1] < rates[i - 1][1]:
            streak += 1
        else:
            break

    if week_chg < -0.15:
        signal, icon = "台幣走強，外資錢在進", "🟢"
    elif week_chg > 0.15:
        signal, icon = "台幣走弱，外資可能撤", "🔴"
    else:
        signal, icon = "匯率持平", "➖"

    return {
        "rate": latest, "day_change": day_chg, "week_change": week_chg,
        "up_streak": streak, "signal": signal, "icon": icon,
    }


def get_futures_oi() -> dict:
    start = (datetime.date.today() - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
    data = fm_query("TaiwanFuturesInstitutionalInvestors", "TX", start)
    if not data:
        return {}

    by_date = {}
    for d in data:
        inv = d.get("institutional_investors", "")
        if "外資" not in str(inv):
            continue
        date = d.get("date")
        try:
            lng = float(d.get("open_interest_balance_long_volume", 0) or 0)
            sht = float(d.get("open_interest_balance_short_volume", 0) or 0)
        except Exception:
            continue
        by_date[date] = lng - sht

    if not by_date:
        return {}

    dates = sorted(by_date.keys())
    latest = by_date[dates[-1]]
    prev = by_date[dates[-2]] if len(dates) >= 2 else latest
    change = latest - prev

    if latest > 5000:
        stance, icon = "外資期貨偏多", "🟢"
    elif latest < -5000:
        stance, icon = "外資期貨偏空", "🔴"
    else:
        stance, icon = "外資期貨中性", "➖"

    return {"net_oi": latest, "change": change, "stance": stance, "icon": icon}


def build_env() -> dict:
    print("收集美股與ADR...")
    us = get_us_indices()
    print("收集匯率...")
    fx = get_fx()
    print("收集外資期貨部位...")
    fut = get_futures_oi()

    score = 0
    sox = us.get("費半", {})
    if not sox.get("missing"):
        c = sox.get("change", 0)
        if c > 1:
            score += 2
        elif c > 0:
            score += 1
        elif c < -1:
            score -= 2
        elif c < 0:
            score -= 1

    adr = us.get("台積電ADR", {})
    if not adr.get("missing"):
        c = adr.get("change", 0)
        if c > 1:
            score += 2
        elif c > 0:
            score += 1
        elif c < -1:
            score -= 2
        elif c < 0:
            score -= 1

    if fx.get("week_change") is not None:
        if fx["week_change"] < -0.15:
            score += 1
        elif fx["week_change"] > 0.15:
            score -= 1

    if fut.get("net_oi") is not None:
        if fut["net_oi"] > 5000:
            score += 1
        elif fut["net_oi"] < -5000:
            score -= 1

    if score >= 3:
        verdict, vicon = "偏多，可積極", "🟢"
    elif score <= -3:
        verdict, vicon = "偏空，宜保守", "🔴"
    else:
        verdict, vicon = "中性，選股為主", "🟡"

    return {"us": us, "fx": fx, "futures": fut,
            "score": score, "verdict": verdict, "verdict_icon": vicon}


def format_env(env: dict) -> str:
    if not env:
        return ""

    lines = ["🌏 今日環境判讀", "═" * 16]
    us = env.get("us", {})

    for name in ["費半", "納斯達克", "道瓊", "標普500"]:
        v = us.get(name)
        if not v:
            continue
        if v.get("missing"):
            lines.append(f"⬜ {name} 資料缺失")
            continue
        icon = "🟢" if v["change"] > 0 else "🔴" if v["change"] < 0 else "➖"
        mark = "＊" if v.get("fallback") else ""
        lines.append(f"{icon} {name} {v['change']:+.2f}%{mark}")

    for name in ["台積電ADR", "聯電ADR"]:
        v = us.get(name)
        if not v:
            continue
        if v.get("missing"):
            lines.append(f"⬜ {name} 資料缺失")
            continue
        icon = "🟢" if v["change"] > 0 else "🔴" if v["change"] < 0 else "➖"
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

    if any(v.get("fallback") for v in us.values() if isinstance(v, dict)):
        lines.append("＊使用備援資料源")

    lines.append("═" * 16)
    lines.append(f"{env['verdict_icon']} 綜合判讀：{env['verdict']}")

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_env(build_env()))
