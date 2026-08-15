import os
import requests
import datetime

TOKEN = os.environ.get("FINMIND_TOKEN", "")
API_URL = "https://api.finmindtrade.com/api/v4/data"

LEVEL_WARN = 0.90    # 維持率150%
LEVEL_CALL = 0.84    # 維持率140%
LEVEL_FORCE = 0.78   # 維持率130%

MAX_ALERT_DEPTH = -15.0  # 只提示現價 -15% 以內的防線


def fm_query(dataset: str, data_id: str = "", start_date: str = "") -> list:
    params = {"dataset": dataset, "token": TOKEN}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    try:
        r = requests.get(API_URL, params=params, timeout=15)
        j = r.json()
        if j.get("status") != 200:
            return []
        return j.get("data", [])
    except Exception:
        return []


def get_margin_analysis(stock_code: str, days: int = 90) -> dict:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

    margin = fm_query("TaiwanStockMarginPurchaseShortSale", stock_code, start)
    price = fm_query("TaiwanStockPrice", stock_code, start)
    if not margin or not price:
        return {}

    price_map = {}
    for p in price:
        if p.get("close") and p.get("max") and p.get("min"):
            avg = (float(p["max"]) + float(p["min"]) + float(p["close"]) * 2) / 4
            price_map[p["date"]] = avg

    batches = []
    prev = None
    for m in margin:
        date = m.get("date")
        bal = m.get("MarginPurchaseTodayBalance")
        if bal is None or date not in price_map:
            continue
        bal = float(bal)
        if prev is not None:
            delta = bal - prev
            if delta > 0:
                batches.append({"add": delta, "cost": price_map[date]})
        prev = bal

    if not batches:
        return {}

    total_add = sum(b["add"] for b in batches)
    avg_cost = sum(b["cost"] * b["add"] for b in batches) / total_add if total_add else 0

    first_bal = float(margin[0].get("MarginPurchaseTodayBalance") or 0)
    last_bal = float(margin[-1].get("MarginPurchaseTodayBalance") or 0)
    bal_change = ((last_bal - first_bal) / first_bal * 100) if first_bal else 0

    recent = margin[-20:] if len(margin) >= 20 else margin
    r_first = float(recent[0].get("MarginPurchaseTodayBalance") or 0)
    r_last = float(recent[-1].get("MarginPurchaseTodayBalance") or 0)
    recent_change = ((r_last - r_first) / r_first * 100) if r_first else 0

    # 融資使用率：餘額 / 限額（兩者單位一致才算，否則不顯示）
    usage = None
    limit = margin[-1].get("MarginPurchaseLimit")
    if limit:
        limit = float(limit)
        if limit > last_bal > 0:
            u = last_bal / limit * 100
            if 0.1 < u < 100:
                usage = u

    return {
        "avg_cost": round(avg_cost, 1),
        "warn_price": round(avg_cost * LEVEL_WARN, 1),
        "call_price": round(avg_cost * LEVEL_CALL, 1),
        "force_price": round(avg_cost * LEVEL_FORCE, 1),
        "balance": last_bal,
        "balance_change": bal_change,
        "recent_change": recent_change,
        "usage": usage,
    }


def get_key_levels(stock_code: str, days: int = 120) -> dict:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    price = fm_query("TaiwanStockPrice", stock_code, start)
    if not price:
        return {}

    lows = [float(p["min"]) for p in price if p.get("min")]
    highs = [float(p["max"]) for p in price if p.get("max")]
    closes = [float(p["close"]) for p in price if p.get("close")]
    volumes = [float(p.get("Trading_Volume", 0)) for p in price]

    if not closes:
        return {}

    current = closes[-1]
    low_30 = min(lows[-30:]) if len(lows) >= 30 else min(lows)
    low_60 = min(lows[-60:-30]) if len(lows) >= 60 else None
    high_recent = max(highs[-60:]) if len(highs) >= 60 else max(highs)

    heavy_zone = 0
    if len(volumes) > 10:
        threshold = sorted(volumes, reverse=True)[max(1, len(volumes) // 5)]
        heavy = [(closes[i], volumes[i]) for i in range(len(closes)) if volumes[i] >= threshold]
        if heavy:
            tv = sum(v for _, v in heavy)
            heavy_zone = round(sum(c * v for c, v in heavy) / tv, 1) if tv else 0

    return {
        "current": current,
        "low_30": low_30,
        "low_60": low_60,
        "high_recent": high_recent,
        "heavy_zone": heavy_zone,
    }


def get_institutional_flow(stock_code: str, days: int = 10) -> dict:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    data = fm_query("TaiwanStockInstitutionalInvestorsBuySell", stock_code, start)
    if not data:
        return {}
    flow = {}
    for d in data:
        name = d.get("name", "")
        net = float(d.get("buy", 0)) - float(d.get("sell", 0))
        flow[name] = flow.get(name, 0) + net
    return {
        "foreign": flow.get("Foreign_Investor", 0) / 1000,
        "trust": flow.get("Investment_Trust", 0) / 1000,
        "dealer": (flow.get("Dealer_self", 0) + flow.get("Dealer_Hedging", 0)) / 1000,
    }


def find_danger_zones(current: float, margin: dict, levels: dict) -> list:
    """只找『融資賣壓 x 價格支撐』的交叉重疊，同類型不算"""
    margin_levels = []
    price_levels = []

    if margin.get("warn_price"):
        margin_levels.append(("融資警戒", margin["warn_price"]))
    if margin.get("call_price"):
        margin_levels.append(("融資追繳", margin["call_price"]))
    if margin.get("force_price"):
        margin_levels.append(("融資斷頭", margin["force_price"]))

    if levels.get("low_30"):
        price_levels.append(("前波低點", levels["low_30"]))
    if levels.get("low_60"):
        price_levels.append(("前低支撐", levels["low_60"]))
    if levels.get("heavy_zone"):
        price_levels.append(("套牢區", levels["heavy_zone"]))

    zones = []
    for mn, mp in margin_levels:
        if mp >= current:
            continue
        for pn, pp in price_levels:
            if pp >= current:
                continue
            if abs(mp - pp) / mp < 0.035:
                price = (mp + pp) / 2
                pct = (price - current) / current * 100
                if pct >= MAX_ALERT_DEPTH:
                    zones.append({
                        "price": price,
                        "pct": pct,
                        "reasons": [mn, pn],
                    })

    zones.sort(key=lambda z: -z["price"])
    return zones[:2]


def build_risk_map(stock_code: str, stock_name: str = "") -> dict:
    margin = get_margin_analysis(stock_code)
    levels = get_key_levels(stock_code)
    flow = get_institutional_flow(stock_code)

    if not levels:
        return {}

    current = levels["current"]

    # 融資戶損益狀態
    margin_pnl = None
    if margin.get("avg_cost"):
        margin_pnl = (current - margin["avg_cost"]) / margin["avg_cost"] * 100

    return {
        "code": stock_code,
        "name": stock_name or stock_code,
        "current": current,
        "margin": margin,
        "margin_pnl": margin_pnl,
        "levels": levels,
        "flow": flow,
        "danger_zones": find_danger_zones(current, margin, levels),
    }


def pct_of(target: float, current: float) -> float:
    return (target - current) / current * 100


def format_risk_map(risk: dict) -> str:
    if not risk:
        return ""

    cur = risk["current"]
    margin = risk.get("margin", {})
    levels = risk.get("levels", {})
    flow = risk.get("flow", {})

    lines = [f"🗺 {risk['name']} {risk['code']}  現價 {cur:.1f}"]
    lines.append("━" * 14)

    # 融資戶損益（判斷賣壓性質）
    pnl = risk.get("margin_pnl")
    if pnl is not None:
        if pnl > 5:
            note = "獲利中，回檔恐了結"
            icon = "⚠️"
        elif pnl < -5:
            note = "套牢中，賣壓已釋放"
            icon = "✅"
        else:
            note = "成本區附近"
            icon = "➖"
        lines.append(f"{icon} 融資戶損益 {pnl:+.1f}%  {note}")

    if margin.get("avg_cost"):
        lines.append(f"融資均成本 {margin['avg_cost']:.0f}")
        lines.append(f"🟡 警戒 {margin['warn_price']:.0f}（{pct_of(margin['warn_price'], cur):+.1f}%）")
        lines.append(f"🟠 追繳 {margin['call_price']:.0f}（{pct_of(margin['call_price'], cur):+.1f}%）")
        lines.append(f"🔴 斷頭 {margin['force_price']:.0f}（{pct_of(margin['force_price'], cur):+.1f}%）")

    lines.append("━" * 14)

    if levels.get("low_30"):
        lines.append(f"📉 前波低 {levels['low_30']:.0f}（{pct_of(levels['low_30'], cur):+.1f}%）")
    if levels.get("heavy_zone"):
        lines.append(f"🔵 套牢區 {levels['heavy_zone']:.0f}（{pct_of(levels['heavy_zone'], cur):+.1f}%）")

    if margin.get("recent_change") is not None:
        rc = margin["recent_change"]
        if rc > 10:
            icon, note = "🔥", "火藥累積"
        elif rc < -10:
            icon, note = "✅", "籌碼沉澱"
        else:
            icon, note = "➖", "持平"
        lines.append(f"{icon} 近20日融資 {rc:+.0f}% {note}")

    if margin.get("usage"):
        lines.append(f"📊 融資使用率 {margin['usage']:.1f}%")

    if flow:
        lines.append(f"🏦 近10日 外資{flow.get('foreign', 0):+.0f} 投信{flow.get('trust', 0):+.0f} 張")

    if risk.get("danger_zones"):
        lines.append("━" * 14)
        for z in risk["danger_zones"]:
            lines.append(f"⚠️ 關鍵防線 {z['price']:.0f}（{z['pct']:+.1f}%）")
            lines.append(f"   {z['reasons'][0]} × {z['reasons'][1]} 交叉")

    return "\n".join(lines)


if __name__ == "__main__":
    for code, name in [("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"), ("3231", "緯創")]:
        print("=" * 40)
        print(format_risk_map(build_risk_map(code, name)))
        print()
