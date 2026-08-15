import os
import requests
import datetime

TOKEN = os.environ.get("FINMIND_TOKEN", "")
API_URL = "https://api.finmindtrade.com/api/v4/data"

# 融資斷頭係數：融資成本 x 0.78 約為維持率130%的斷頭價
MARGIN_CALL_RATIO = 0.78


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


def get_margin_zones(stock_code: str, days: int = 60) -> dict:
    """推估融資斷頭引爆區"""
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

    margin = fm_query("TaiwanStockMarginPurchaseShortSale", stock_code, start)
    price = fm_query("TaiwanStockPrice", stock_code, start)
    if not margin or not price:
        return {}

    # 日期對應收盤價
    price_map = {p["date"]: float(p.get("close", 0)) for p in price if p.get("close")}

    # 找融資增加的日子，記錄「增加張數」與「當日均價」
    zones = []
    prev_balance = None
    for m in margin:
        date = m.get("date")
        bal = m.get("MarginPurchaseTodayBalance")
        if bal is None or date not in price_map:
            continue
        bal = float(bal)
        if prev_balance is not None:
            delta = bal - prev_balance
            if delta > 0:
                zones.append({
                    "date": date,
                    "add": delta,
                    "cost": price_map[date],
                    "call_price": round(price_map[date] * MARGIN_CALL_RATIO, 1),
                })
        prev_balance = bal

    if not zones:
        return {}

    # 融資水位變化
    first_bal = float(margin[0].get("MarginPurchaseTodayBalance") or 0)
    last_bal = float(margin[-1].get("MarginPurchaseTodayBalance") or 0)
    bal_change = ((last_bal - first_bal) / first_bal * 100) if first_bal else 0

    # 用「增加張數」加權，找出斷頭價密集區
    total_add = sum(z["add"] for z in zones)
    weighted_call = sum(z["call_price"] * z["add"] for z in zones) / total_add if total_add else 0

    # 取增量最大的前5批，看斷頭價範圍
    top_zones = sorted(zones, key=lambda x: x["add"], reverse=True)[:5]
    call_prices = [z["call_price"] for z in top_zones]

    return {
        "margin_balance": last_bal,
        "balance_change_pct": bal_change,
        "call_zone_low": min(call_prices),
        "call_zone_high": max(call_prices),
        "call_zone_center": round(weighted_call, 1),
        "hot_batches": top_zones,
        "total_added": total_add,
    }


def get_key_levels(stock_code: str, days: int = 90) -> dict:
    """找前波低點與大量套牢區"""
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    price = fm_query("TaiwanStockPrice", stock_code, start)
    if not price:
        return {}

    lows = [float(p["min"]) for p in price if p.get("min")]
    closes = [float(p["close"]) for p in price if p.get("close")]
    volumes = [float(p.get("Trading_Volume", 0)) for p in price]

    if not lows or not closes:
        return {}

    current = closes[-1]

    # 前波低點：近30日最低
    recent_low = min(lows[-30:]) if len(lows) >= 30 else min(lows)

    # 大量套牢區：成交量前20%的日子，其收盤價的加權平均
    if volumes and len(volumes) > 10:
        threshold = sorted(volumes, reverse=True)[max(1, len(volumes) // 5)]
        heavy = [(closes[i], volumes[i]) for i in range(len(closes)) if volumes[i] >= threshold]
        if heavy:
            tv = sum(v for _, v in heavy)
            heavy_zone = round(sum(c * v for c, v in heavy) / tv, 1) if tv else 0
        else:
            heavy_zone = 0
    else:
        heavy_zone = 0

    return {
        "current": current,
        "recent_low": recent_low,
        "heavy_zone": heavy_zone,
    }


def get_institutional_flow(stock_code: str, days: int = 10) -> dict:
    """近期三大法人動向"""
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    data = fm_query("TaiwanStockInstitutionalInvestorsBuySell", stock_code, start)
    if not data:
        return {}

    flow = {}
    for d in data:
        name = d.get("name", "")
        net = float(d.get("buy", 0)) - float(d.get("sell", 0))
        flow[name] = flow.get(name, 0) + net

    foreign = flow.get("Foreign_Investor", 0)
    trust = flow.get("Investment_Trust", 0)
    dealer = flow.get("Dealer_self", 0) + flow.get("Dealer_Hedging", 0)

    return {
        "foreign": foreign / 1000,
        "trust": trust / 1000,
        "dealer": dealer / 1000,
    }


def build_risk_map(stock_code: str, stock_name: str = "") -> dict:
    """組合完整風險地圖"""
    margin = get_margin_zones(stock_code)
    levels = get_key_levels(stock_code)
    flow = get_institutional_flow(stock_code)

    if not levels:
        return {}

    current = levels["current"]

    result = {
        "code": stock_code,
        "name": stock_name or stock_code,
        "current": current,
        "levels": levels,
        "margin": margin,
        "flow": flow,
        "warnings": [],
    }

    # 距離前波低點
    if levels.get("recent_low"):
        pct = (levels["recent_low"] - current) / current * 100
        result["low_distance_pct"] = pct

    # 距離斷頭引爆區
    if margin.get("call_zone_center"):
        center = margin["call_zone_center"]
        pct = (center - current) / current * 100
        result["call_distance_pct"] = pct
        if -15 < pct < 0:
            result["warnings"].append(f"融資引爆區在 {pct:.1f}% 處")

    # 融資水位警示
    if margin.get("balance_change_pct"):
        chg = margin["balance_change_pct"]
        if chg > 15:
            result["warnings"].append(f"融資暴增 {chg:.0f}%，籌碼浮動大")
        elif chg < -15:
            result["warnings"].append(f"融資減 {abs(chg):.0f}%，籌碼沉澱")

    return result


def format_risk_map(risk: dict) -> str:
    """格式化成 LINE 訊息"""
    if not risk:
        return ""

    lines = [f"🗺 {risk['name']} {risk['code']}"]
    lines.append(f"現價 {risk['current']:.1f}")
    lines.append("─" * 16)

    margin = risk.get("margin", {})
    levels = risk.get("levels", {})
    flow = risk.get("flow", {})

    # 融資引爆區
    if margin.get("call_zone_center"):
        low = margin["call_zone_low"]
        high = margin["call_zone_high"]
        pct = risk.get("call_distance_pct", 0)
        lines.append(f"🔴 融資引爆區 {low:.0f}~{high:.0f}")
        lines.append(f"   距現價 {pct:.1f}%")

    # 前波低點
    if levels.get("recent_low"):
        pct = risk.get("low_distance_pct", 0)
        lines.append(f"🟡 前波低點 {levels['recent_low']:.0f}（{pct:.1f}%）")

    # 套牢區
    if levels.get("heavy_zone"):
        lines.append(f"🔵 大量套牢區 {levels['heavy_zone']:.0f}")

    # 融資水位
    if margin.get("balance_change_pct") is not None:
        chg = margin["balance_change_pct"]
        icon = "⚠️" if chg > 15 else "✅" if chg < -10 else "➖"
        lines.append(f"{icon} 融資水位 {chg:+.0f}%（火藥量）")

    # 法人動向
    if flow:
        f = flow.get("foreign", 0)
        t = flow.get("trust", 0)
        lines.append(f"🏦 近10日 外資{f:+.0f}張 投信{t:+.0f}張")

    # 警示
    if risk.get("warnings"):
        lines.append("─" * 16)
        for w in risk["warnings"]:
            lines.append(f"💡 {w}")

    return "\n".join(lines)


if __name__ == "__main__":
    for code, name in [("2330", "台積電"), ("2317", "鴻海")]:
        print("=" * 40)
        risk = build_risk_map(code, name)
        print(format_risk_map(risk))
        print()
