import os
import requests
import datetime

TOKEN = os.environ.get("FINMIND_TOKEN", "")
API_URL = "https://api.finmindtrade.com/api/v4/data"

# 融資維持率分層
LEVEL_WARN = 0.90    # 維持率150%
LEVEL_CALL = 0.84    # 維持率140%
LEVEL_FORCE = 0.78   # 維持率130%

MERGE_PCT = 0.03     # 3%內視為同一階
MAX_DEPTH = 25.0     # 只看 ±25% 以內
ATR_DAYS = 5         # ATR推估天數


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


def get_price_history(stock_code: str, days: int = 180) -> list:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for p in fm_query("TaiwanStockPrice", stock_code, start):
        try:
            out.append({
                "date": p["date"],
                "close": float(p["close"]),
                "high": float(p["max"]),
                "low": float(p["min"]),
                "volume": float(p.get("Trading_Volume", 0)) / 1000,
            })
        except Exception:
            continue
    return out


def get_margin_analysis(stock_code: str, hist: list, days: int = 90) -> dict:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    margin = fm_query("TaiwanStockMarginPurchaseShortSale", stock_code, start)
    if not margin or not hist:
        return {}

    price_map = {h["date"]: (h["high"] + h["low"] + h["close"] * 2) / 4 for h in hist}

    batches, prev = [], None
    for m in margin:
        date, bal = m.get("date"), m.get("MarginPurchaseTodayBalance")
        if bal is None or date not in price_map:
            continue
        bal = float(bal)
        if prev is not None and bal - prev > 0:
            batches.append({"add": bal - prev, "cost": price_map[date]})
        prev = bal

    if not batches:
        return {}

    total = sum(b["add"] for b in batches)
    avg_cost = sum(b["cost"] * b["add"] for b in batches) / total if total else 0

    recent = margin[-20:] if len(margin) >= 20 else margin
    rf = float(recent[0].get("MarginPurchaseTodayBalance") or 0)
    rl = float(recent[-1].get("MarginPurchaseTodayBalance") or 0)
    recent_change = ((rl - rf) / rf * 100) if rf else 0

    return {
        "avg_cost": avg_cost,
        "warn_price": avg_cost * LEVEL_WARN,
        "call_price": avg_cost * LEVEL_CALL,
        "force_price": avg_cost * LEVEL_FORCE,
        "recent_change": recent_change,
    }


def calc_ma(hist: list, n: int) -> float:
    if len(hist) < n:
        return 0
    return sum(h["close"] for h in hist[-n:]) / n


def find_volume_zones(hist: list) -> dict:
    if len(hist) < 20:
        return {}
    vols = [h["volume"] for h in hist]
    thr = sorted(vols, reverse=True)[max(1, len(vols) // 4)]
    heavy = [h for h in hist if h["volume"] >= thr]
    if not heavy:
        return {}
    prices = sorted(h["close"] for h in heavy)
    n = len(prices)
    return {
        "low": prices[int(n * 0.25)],
        "mid": prices[int(n * 0.5)],
        "high": prices[min(int(n * 0.75), n - 1)],
    }


def round_levels(current: float) -> list:
    if current >= 1000:
        step = 100
    elif current >= 500:
        step = 50
    elif current >= 100:
        step = 10
    elif current >= 50:
        step = 5
    else:
        step = 1
    base = int(current / step) * step
    return [base - step, base, base + step, base + step * 2]


def collect_levels(current: float, hist: list, margin: dict) -> list:
    """收集所有支撐壓力候選點"""
    highs = [h["high"] for h in hist]
    lows = [h["low"] for h in hist]
    vz = find_volume_zones(hist)
    raw = []

    for n, label in [(20, "月線"), (60, "季線"), (120, "半年線")]:
        ma = calc_ma(hist, n)
        if ma > 0:
            raw.append((label, ma, "均線"))

    if len(hist) >= 20:
        raw.append(("20日高", max(highs[-20:]), "價格"))
        raw.append(("20日低", min(lows[-20:]), "價格"))
    if len(hist) >= 60:
        raw.append(("季高", max(highs[-60:]), "價格"))
        raw.append(("季低", min(lows[-60:]), "價格"))
    if len(hist) >= 120:
        raw.append(("半年高", max(highs[-120:]), "價格"))
        raw.append(("半年低", min(lows[-120:]), "價格"))

    if vz:
        raw.append(("套牢上緣", vz["high"], "籌碼"))
        raw.append(("套牢核心", vz["mid"], "籌碼"))
        raw.append(("套牢下緣", vz["low"], "籌碼"))

    if margin.get("warn_price"):
        raw.append(("融資警戒", margin["warn_price"], "融資"))
        raw.append(("融資追繳", margin["call_price"], "融資"))
        raw.append(("融資斷頭", margin["force_price"], "融資"))

    for r in round_levels(current):
        if r > 0:
            raw.append((f"整數{r:.0f}", r, "心理"))

    return raw


def cluster_levels(raw: list, current: float, above: bool) -> list:
    """把相近的價位合併成同一階"""
    items = [x for x in raw if (x[1] > current) == above]
    items = [x for x in items if abs(x[1] - current) / current * 100 <= MAX_DEPTH]
    items.sort(key=lambda x: x[1], reverse=not above)

    merged = []
    for name, price, kind in items:
        placed = False
        for m in merged:
            if abs(m["price"] - price) / m["price"] < MERGE_PCT:
                m["labels"].append(name)
                m["kinds"].add(kind)
                n = len(m["labels"])
                m["price"] = (m["price"] * (n - 1) + price) / n
                placed = True
                break
        if not placed:
            merged.append({
                "price": price,
                "labels": [name],
                "kinds": {kind},
                "estimated": False,
            })

    for m in merged:
        m["pct"] = (m["price"] - current) / current * 100
        m["strength"] = len(m["kinds"])

    return merged[:4]


def add_atr_estimates(resistance: list, current: float, atr: float) -> list:
    """上方壓力不足時補推估值（真實壓力優先，推估只補在後面）"""
    if atr <= 0:
        return resistance

    real_count = len([r for r in resistance if not r["estimated"]])
    if real_count >= 2:
        return resistance

    # 5日合理波動 = ATR x sqrt(5)
    est_move = atr * (ATR_DAYS ** 0.5)
    # 完全無真實壓力補2階，有1階真實補1階
    multipliers = [1.0, 1.5] if real_count == 0 else [1.0]

    for mult in multipliers:
        price = current * (1 + est_move * mult / 100)
        # 不與現有階位重疊
        if all(abs(price - r["price"]) / price > MERGE_PCT for r in resistance):
            resistance.append({
                "price": price,
                "labels": [f"ATR{ATR_DAYS}日推估"],
                "kinds": {"推估"},
                "pct": (price - current) / current * 100,
                "strength": 1,
                "estimated": True,
            })

    resistance.sort(key=lambda x: x["price"])
    return resistance[:4]


def evaluate_rr(resistance: list, support: list, margin_pnl) -> dict:
    """
    盈虧比評估
    信心判斷只看『實際用來計算的那一階』是否為推估值
    """
    if not resistance or not support:
        return {"value": None}

    r0 = resistance[0]
    s0 = support[0]
    up = r0["pct"]
    down = abs(s0["pct"])
    if down <= 0:
        return {"value": None}

    rr = up / down

    # 信心分級
    if r0["estimated"]:
        confidence = "low"
        note = "上方無真實壓力，數值僅供參考"
    elif margin_pnl is not None and margin_pnl > 15:
        confidence = "mid"
        note = "融資獲利高，賣壓可能提前出現"
    else:
        confidence = "high"
        note = ""

    return {
        "value": rr,
        "confidence": confidence,
        "note": note,
        "up_price": r0["price"],
        "up_pct": up,
        "down_price": s0["price"],
        "down_pct": s0["pct"],
    }


def build_risk_map(stock_code: str, stock_name: str = "") -> dict:
    hist = get_price_history(stock_code)
    if not hist:
        return {}

    current = hist[-1]["close"]

    ranges = [(h["high"] - h["low"]) / h["close"] * 100 for h in hist[-20:] if h["close"] > 0]
    atr = sum(ranges) / len(ranges) if ranges else 0

    margin = get_margin_analysis(stock_code, hist)
    raw = collect_levels(current, hist, margin)

    resistance = cluster_levels(raw, current, above=True)
    support = cluster_levels(raw, current, above=False)
    resistance = add_atr_estimates(resistance, current, atr)

    margin_pnl = None
    if margin.get("avg_cost"):
        margin_pnl = (current - margin["avg_cost"]) / margin["avg_cost"] * 100

    rr = evaluate_rr(resistance, support, margin_pnl)

    return {
        "code": stock_code,
        "name": stock_name or stock_code,
        "current": current,
        "atr": atr,
        "margin": margin,
        "margin_pnl": margin_pnl,
        "ladder": {"resistance": resistance, "support": support},
        "rr": rr,
    }


def strength_icon(n: int) -> str:
    return "▓" * min(n, 3) + "░" * max(0, 3 - n)


def format_ladder(risk: dict) -> str:
    if not risk:
        return ""

    cur = risk["current"]
    res = risk.get("ladder", {}).get("resistance", [])
    sup = risk.get("ladder", {}).get("support", [])
    lines = []

    if res:
        lines.append("📈 上檔壓力")
        for r in reversed(res):
            labels = "/".join(r["labels"][:2])
            mark = " ※推估" if r["estimated"] else ""
            lines.append(f" {strength_icon(r['strength'])} {r['price']:.1f}（{r['pct']:+.1f}%）{labels}{mark}")
        if len(res) >= 2:
            lines.append(f"   ▲ 突破{res[0]['price']:.1f} → 直攻{res[1]['price']:.1f}")

    lines.append(f"━━ 現價 {cur:.1f} ━━")

    if sup:
        if len(sup) >= 2:
            lines.append(f"   ▼ 跌破{sup[0]['price']:.1f} → 直落{sup[1]['price']:.1f}")
        lines.append("📉 下檔支撐")
        for s in sup:
            labels = "/".join(s["labels"][:2])
            lines.append(f" {strength_icon(s['strength'])} {s['price']:.1f}（{s['pct']:+.1f}%）{labels}")

    rr = risk.get("rr", {})
    if rr.get("value") is not None:
        v = rr["value"]
        conf = rr["confidence"]
        if conf == "low":
            icon, verdict = "❓", "參考值"
        elif v >= 1.5:
            icon, verdict = "✅", "划算"
        elif v < 0.8:
            icon, verdict = "⚠️", "偏差，空間不足"
        else:
            icon, verdict = "➖", "普通"
        lines.append(f"⚖️ 盈虧比 1:{v:.2f} {icon} {verdict}")
        lines.append(f"   上 {rr['up_pct']:+.1f}% / 下 {rr['down_pct']:+.1f}%")
        if rr.get("note"):
            lines.append(f"   ※{rr['note']}")

    pnl = risk.get("margin_pnl")
    if pnl is not None:
        if pnl > 15:
            lines.append(f"⚠️ 融資獲利{pnl:+.0f}%，上方了結賣壓重")
        elif pnl > 5:
            lines.append(f"⚠️ 融資獲利{pnl:+.0f}%，回檔恐了結")
        elif pnl < -5:
            lines.append(f"✅ 融資套牢{pnl:+.0f}%，賣壓已釋放")

    m = risk.get("margin", {})
    rc = m.get("recent_change")
    if rc is not None:
        if rc > 30:
            lines.append(f"🔥 融資{rc:+.0f}% 火藥大量累積，慎入")
        elif rc > 10:
            lines.append(f"🔥 融資{rc:+.0f}% 火藥累積")
        elif rc < -10:
            lines.append(f"✅ 融資{rc:+.0f}% 籌碼沉澱")

    return "\n".join(x for x in lines if x)


def format_risk_map(risk: dict) -> str:
    if not risk:
        return ""
    head = f"🗺 {risk['name']} {risk['code']}  現價 {risk['current']:.1f}  波動{risk['atr']:.1f}%"
    return head + "\n" + format_ladder(risk)


if __name__ == "__main__":
    for code, name in [("2347", "聯強"), ("3017", "奇鋐"), ("2376", "技嘉"), ("6213", "聯茂")]:
        print("=" * 44)
        print(format_risk_map(build_risk_map(code, name)))
        print()
