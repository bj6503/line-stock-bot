import os
import requests
import datetime
import time

TOKEN = os.environ.get("FINMIND_TOKEN", "")
API_URL = "https://api.finmindtrade.com/api/v4/data"

# ===== 觀察名單（改名單請改 categories.py，不要改這裡）=====
# categories.py 缺席時自動退回原本的 11 支，不會讓整支程式掛掉。
try:
    from categories import CORE, WATCH
    from categories import category as _category
    from categories import is_new_listing as _is_new_listing
    from categories import by_category as _by_category
    _HAS_CAT = True
except ImportError:
    CORE = [
        "1519", "7799", "2454", "6526", "2308",
        "5351", "6239", "6841", "7887", "6223", "3081",
    ]
    WATCH = []
    _HAS_CAT = False

    def _category(code):
        return ""

    def _is_new_listing(code):
        return False

    def _by_category(codes):
        return [("", list(codes))]

WATCHLIST = CORE + WATCH

BB_PERIOD = 20
BB_STD = 2.0
SQUEEZE_PCT = 25

# 帶寬分位數要比對的樣本數（「近120日」的120就是這個）
BW_WINDOW = 120
# 套牢區取樣窗口，固定住才不會隨抓取天數浮動
VZ_WINDOW = 120
# 抓幾個「日曆天」。BW_WINDOW 需要 120+BB_PERIOD-1 = 139 個「交易日」，
# 換算日曆天約 207 天。原本設 200 只拿得到約 118 筆帶寬，永遠差一點，
# 導致每支都被判定資料不足。拉到 300 留足餘裕（約 182 筆）。
HIST_DAYS = 300
VOLUME_MULTIPLE = 2.0
VOLUME_WARN = 1.5
MIN_BODY_PCT = 2.0
DIVERGE_VOL = 3.0

LEVEL_WARN = 0.90
LEVEL_CALL = 0.84
LEVEL_FORCE = 0.78

MERGE_PCT = 0.03
MAX_DEPTH = 25.0
MIN_GAP_PCT = 0.5
ATR_DAYS = 5

SELL_STREAK_WARN = 2  # 法人連賣幾日視為風險


def fm_query(dataset: str, data_id: str = "", start_date: str = "") -> list:
    params = {"dataset": dataset, "token": TOKEN}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    try:
        j = requests.get(API_URL, params=params, timeout=20).json()
        if j.get("status") != 200:
            return []
        return j.get("data", [])
    except Exception:
        return []


def get_stock_name(code: str) -> str:
    data = fm_query("TaiwanStockInfo", code)
    for d in data:
        n = d.get("stock_name")
        if n:
            return str(n).replace("*", "").strip()
    return code


def get_history(code: str, days: int = HIST_DAYS) -> list:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for p in fm_query("TaiwanStockPrice", code, start):
        try:
            out.append({
                "date": p["date"],
                "open": float(p["open"]),
                "close": float(p["close"]),
                "high": float(p["max"]),
                "low": float(p["min"]),
                "volume": float(p.get("Trading_Volume", 0)) / 1000,
            })
        except Exception:
            continue
    return out


def get_margin(code: str, hist: list, days: int = 90) -> dict:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    margin = fm_query("TaiwanStockMarginPurchaseShortSale", code, start)
    if not margin or not hist:
        return {}
    price_map = {h["date"]: (h["high"] + h["low"] + h["close"] * 2) / 4 for h in hist}
    batches, prev = [], None
    for m in margin:
        d, bal = m.get("date"), m.get("MarginPurchaseTodayBalance")
        if bal is None or d not in price_map:
            continue
        bal = float(bal)
        if prev is not None and bal - prev > 0:
            batches.append({"add": bal - prev, "cost": price_map[d]})
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
        "balance": rl,
    }


def get_flow(code: str, days: int = 12) -> dict:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    data = fm_query("TaiwanStockInstitutionalInvestorsBuySell", code, start)
    if not data:
        return {}
    by_date = {}
    for d in data:
        date = d.get("date")
        name = d.get("name", "")
        net = (float(d.get("buy", 0)) - float(d.get("sell", 0))) / 1000
        e = by_date.setdefault(date, {"foreign": 0.0, "trust": 0.0, "dealer": 0.0})
        if name == "Foreign_Investor":
            e["foreign"] += net
        elif name == "Investment_Trust":
            e["trust"] += net
        elif name.startswith("Dealer"):
            e["dealer"] += net
    if not by_date:
        return {}
    dates = sorted(by_date.keys())
    last = by_date[dates[-1]]

    def streak(key, positive=True):
        s = 0
        for d in reversed(dates):
            v = by_date[d][key]
            if (v > 0) if positive else (v < 0):
                s += 1
            else:
                break
        return s

    return {
        "date": dates[-1],
        "foreign": last["foreign"],
        "trust": last["trust"],
        "dealer": last["dealer"],
        "f_streak": streak("foreign", True),
        "t_streak": streak("trust", True),
        "f_sell_streak": streak("foreign", False),
        "t_sell_streak": streak("trust", False),
        "f_total": sum(by_date[d]["foreign"] for d in dates),
        "t_total": sum(by_date[d]["trust"] for d in dates),
    }


def calc_bb(closes: list) -> dict:
    if len(closes) < BB_PERIOD:
        return {}
    w = closes[-BB_PERIOD:]
    mid = sum(w) / BB_PERIOD
    std = (sum((c - mid) ** 2 for c in w) / BB_PERIOD) ** 0.5
    up, lo = mid + BB_STD * std, mid - BB_STD * std
    return {"upper": up, "mid": mid, "lower": lo,
            "bw": (up - lo) / mid * 100 if mid > 0 else 0}


def percentile(vals: list, p: float) -> float:
    if not vals:
        return 0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def analyze_bb(hist: list) -> dict:
    if len(hist) < BB_PERIOD + 15:
        return {}
    closes = [h["close"] for h in hist]
    bws = []
    for i in range(BB_PERIOD, len(closes) + 1):
        b = calc_bb(closes[:i])
        if b:
            bws.append(b["bw"])
    if len(bws) < 15:
        return {}
    window = bws[-BW_WINDOW:] if len(bws) >= BW_WINDOW else bws
    cur = calc_bb(closes)
    thr = percentile(window, SQUEEZE_PCT)
    in_sq = cur["bw"] <= thr

    sq_days = 0
    for b in reversed(bws):
        if b <= thr:
            sq_days += 1
        else:
            break
    sq_before = 0
    for i in range(len(bws) - 2, -1, -1):
        if bws[i] <= thr:
            sq_before += 1
        else:
            break
    recent = bws[-6:-1] if len(bws) > 6 else bws[:-1]
    was_sq = in_sq or any(b <= thr for b in recent)

    today, prev = hist[-1], hist[-2]
    red = today["close"] > today["open"]
    body = (today["close"] - today["open"]) / today["open"] * 100 if today["open"] > 0 else 0
    vol_r = today["volume"] / prev["volume"] if prev["volume"] > 0 else 0
    prior = [h["high"] for h in hist[-11:-1]]
    brk_high = today["close"] > max(prior) if prior else False
    brk_up = today["close"] > cur["upper"]
    brk_down = today["close"] < cur["lower"]
    brk = brk_high or brk_up
    bw_rank = sum(1 for b in window if b < cur["bw"]) / len(window) * 100
    strong = red and body >= MIN_BODY_PCT
    diverge = vol_r >= DIVERGE_VOL and body < MIN_BODY_PCT
    span = cur["upper"] - cur["lower"]
    pos = ((today["close"] - cur["lower"]) / span * 100) if span > 0 else 50

    if was_sq and strong and vol_r >= VOLUME_MULTIPLE and brk:
        sig, icon, rank = "縮口噴出", "🚀", 0
    elif was_sq and strong and vol_r >= VOLUME_WARN and brk:
        sig, icon, rank = "準噴出", "⚡", 1
    elif diverge:
        sig, icon, rank = "量價背離", "⚠️", 2
    elif was_sq and brk_down and vol_r >= VOLUME_WARN:
        sig, icon, rank = "破下軌", "🔻", 5
    elif in_sq and sq_days >= 8:
        sig, icon, rank = "極度縮口", "🔵", 3
    elif in_sq and sq_days >= 4:
        sig, icon, rank = "縮口中", "🔹", 4
    else:
        sig, icon, rank = "", "", 9

    return {
        "upper": cur["upper"], "mid": cur["mid"], "lower": cur["lower"],
        "bw": cur["bw"], "bw_rank": bw_rank, "position": pos,
        "in_squeeze": in_sq, "squeeze_days": sq_days, "squeeze_before": sq_before,
        "body": body, "vol_ratio": vol_r, "red": red,
        "break_high": brk_high, "break_upper": brk_up, "break_lower": brk_down,
        "signal": sig, "icon": icon, "rank": rank,
        # 分位數樣本數：< BW_WINDOW 表示資料不足，縮口判定可信度打折
        "window_size": len(window),
    }


def calc_ma(hist: list, n: int) -> float:
    if len(hist) < n:
        return 0
    return sum(h["close"] for h in hist[-n:]) / n


def volume_zones(hist: list) -> dict:
    if len(hist) < 20:
        return {}
    # 只看最近 VZ_WINDOW 根，避免套牢區隨抓取天數浮動
    hist = hist[-VZ_WINDOW:]
    vols = [h["volume"] for h in hist]
    thr = sorted(vols, reverse=True)[max(1, len(vols) // 4)]
    heavy = [h for h in hist if h["volume"] >= thr]
    if not heavy:
        return {}
    ps = sorted(h["close"] for h in heavy)
    n = len(ps)
    return {"low": ps[int(n * 0.25)], "mid": ps[int(n * 0.5)],
            "high": ps[min(int(n * 0.75), n - 1)]}


def round_levels(cur: float) -> list:
    if cur >= 1000:
        step = 100
    elif cur >= 500:
        step = 50
    elif cur >= 100:
        step = 10
    elif cur >= 50:
        step = 5
    else:
        step = 1
    base = int(cur / step) * step
    return [base - step, base, base + step, base + step * 2]


def build_ladder(cur: float, hist: list, margin: dict, atr: float, bb: dict) -> dict:
    highs = [h["high"] for h in hist]
    lows = [h["low"] for h in hist]
    vz = volume_zones(hist)
    raw = []

    for n, lbl in [(20, "月線"), (60, "季線"), (120, "半年線")]:
        ma = calc_ma(hist, n)
        if ma > 0:
            raw.append((lbl, ma, "均線"))
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
    if bb.get("upper"):
        raw.append(("布林上軌", bb["upper"], "布林"))
        raw.append(("布林下軌", bb["lower"], "布林"))
    for r in round_levels(cur):
        if r > 0:
            raw.append((f"整數{r:.0f}", r, "心理"))

    def cluster(above: bool):
        items = []
        for name, price, kind in raw:
            if (price > cur) != above:
                continue
            gap = abs(price - cur) / cur * 100
            if gap < MIN_GAP_PCT or gap > MAX_DEPTH:
                continue
            items.append((name, price, kind))
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
                merged.append({"price": price, "labels": [name],
                               "kinds": {kind}, "estimated": False})
        for m in merged:
            m["pct"] = (m["price"] - cur) / cur * 100
            m["strength"] = len(m["kinds"])
        return merged[:4]

    res, sup = cluster(True), cluster(False)

    real = len([r for r in res if not r["estimated"]])
    if real < 2 and atr > 0:
        move = atr * (ATR_DAYS ** 0.5)
        mults = [1.0, 1.5] if real == 0 else [1.0]
        for mt in mults:
            p = cur * (1 + move * mt / 100)
            if all(abs(p - r["price"]) / p > MERGE_PCT for r in res):
                res.append({"price": p, "labels": [f"ATR{ATR_DAYS}日推估"],
                            "kinds": {"推估"}, "pct": (p - cur) / cur * 100,
                            "strength": 1, "estimated": True})
        res.sort(key=lambda x: x["price"])
        res = res[:4]

    return {"resistance": res, "support": sup}


def analyze(code: str) -> dict:
    hist = get_history(code)
    if len(hist) < 40:
        return {"code": code, "error": "資料不足"}

    name = get_stock_name(code)
    cur = hist[-1]["close"]
    prev_close = hist[-2]["close"]
    chg = (cur - prev_close) / prev_close * 100 if prev_close > 0 else 0

    ranges = [(h["high"] - h["low"]) / h["close"] * 100 for h in hist[-20:] if h["close"] > 0]
    atr = sum(ranges) / len(ranges) if ranges else 0

    margin = get_margin(code, hist)
    flow = get_flow(code)
    bb = analyze_bb(hist)
    ladder = build_ladder(cur, hist, margin, atr, bb)

    margin_pnl = None
    if margin.get("avg_cost"):
        margin_pnl = (cur - margin["avg_cost"]) / margin["avg_cost"] * 100

    res, sup = ladder["resistance"], ladder["support"]
    rr = None
    rr_conf = "high"
    if res and sup:
        up, down = res[0]["pct"], abs(sup[0]["pct"])
        if down > 0:
            rr = up / down
        if res[0]["estimated"]:
            rr_conf = "low"
        elif margin_pnl is not None and margin_pnl > 15:
            rr_conf = "mid"

    score = 0
    if flow:
        if flow["foreign"] > 0:
            score += min(flow["f_streak"], 5)
        if flow["trust"] > 0:
            score += min(flow["t_streak"], 5) * 1.5
        if flow["foreign"] > 0 and flow["trust"] > 0:
            score += 4
        if flow["f_sell_streak"] >= SELL_STREAK_WARN:
            score -= flow["f_sell_streak"]
        if flow["t_sell_streak"] >= SELL_STREAK_WARN:
            score -= flow["t_sell_streak"] * 1.5
    if bb:
        score += {0: 10, 1: 7, 2: -5, 3: 6, 4: 4, 5: -6}.get(bb["rank"], 0)
    if rr is not None and rr_conf != "low":
        if rr >= 2:
            score += 5
        elif rr >= 1.5:
            score += 3
        elif rr < 0.8:
            score -= 4
    rc = margin.get("recent_change")
    if rc is not None:
        if rc > 30:
            score -= 4
        elif rc < -10:
            score += 2
    if margin_pnl is not None and margin_pnl > 15:
        score -= 2

    # 資料是否足以支撐縮口判定：分位樣本 <120 就標記
    thin = bool(bb) and bb.get("window_size", 999) < BW_WINDOW
    if _is_new_listing(code):
        thin = True

    return {
        "code": code, "name": name, "close": cur, "chg": chg,
        "data_date": hist[-1]["date"],
        "prev_date": hist[-2]["date"],
        "atr": atr, "volume": hist[-1]["volume"],
        "margin": margin, "margin_pnl": margin_pnl,
        "flow": flow, "bb": bb, "ladder": ladder,
        "rr": rr, "rr_conf": rr_conf, "score": score,
        "category": _category(code),
        "tier": "core" if code in CORE else "watch",
        "thin_data": thin,
        "bars": len(hist),
        "bw_window": bb.get("window_size", 0) if bb else 0,
    }


def analyze_all(codes: list = None) -> list:
    codes = codes or WATCHLIST
    out = []
    for i, c in enumerate(codes, 1):
        tag = "核心" if c in CORE else "觀察"
        print(f"  [{i}/{len(codes)}] 分析 {c} [{tag}] ...")
        r = analyze(c)
        if not r.get("error"):
            out.append(r)
        else:
            print(f"    ⚠️ {c} {r['error']}")
        time.sleep(0.4)
    out.sort(key=lambda x: -x["score"])
    return out


def core_results(results: list) -> list:
    return [a for a in results if a.get("tier") == "core"]


def watch_results(results: list) -> list:
    return [a for a in results if a.get("tier") != "core"]


def verdict(a: dict) -> dict:
    bb = a.get("bb", {})
    margin = a.get("margin", {})
    flow = a.get("flow", {})
    rr = a.get("rr")
    rr_conf = a.get("rr_conf", "high")
    pnl = a.get("margin_pnl")
    rc = margin.get("recent_change")
    rank = bb.get("rank", 9)

    risks, goods = [], []

    # ===== 風險 =====
    if rr is not None and rr_conf != "low" and rr < 0.8:
        risks.append("上檔空間不足")
    if pnl is not None and pnl > 15:
        risks.append("融資獲利高")
    if rc is not None and rc > 25:
        risks.append("融資暴增")
    if rank == 2:
        risks.append("量價背離")
    if rank == 5:
        risks.append("跌破下軌")
    if a["chg"] < -5:
        risks.append("今日重挫")
    fss = flow.get("f_sell_streak", 0)
    tss = flow.get("t_sell_streak", 0)
    if fss >= SELL_STREAK_WARN:
        risks.append(f"外資連{fss}賣")
    if tss >= SELL_STREAK_WARN:
        risks.append(f"投信連{tss}賣")

    # ===== 優點 =====
    if rr is not None and rr_conf != "low" and rr >= 1.5:
        goods.append("盈虧比佳")
    if pnl is not None and pnl < -5:
        goods.append("融資套牢")
    if rc is not None and rc < -10:
        goods.append("籌碼沉澱")
    if flow:
        if flow.get("t_streak", 0) >= 3 and flow.get("trust", 0) > 0:
            goods.append(f"投信連{flow['t_streak']}買")
        elif flow.get("f_streak", 0) >= 3 and flow.get("foreign", 0) > 0:
            goods.append(f"外資連{flow['f_streak']}買")
    if rank == 0:
        goods.append("縮口噴出")
    elif rank == 1:
        goods.append("準噴出")
    elif rank == 3:
        goods.append(f"極度縮口{bb.get('squeeze_days', 0)}日")
    elif rank == 4:
        goods.append(f"縮口{bb.get('squeeze_days', 0)}日")

    if len(risks) >= 2:
        return {"level": "avoid", "icon": "🔴", "text": "避開",
                "reason": "、".join(risks[:2])}
    if risks and not goods:
        return {"level": "avoid", "icon": "🔴", "text": "避開",
                "reason": risks[0]}
    if len(goods) >= 2 and not risks:
        return {"level": "watch", "icon": "🟢", "text": "可留意",
                "reason": "、".join(goods[:2])}
    if goods and risks:
        return {"level": "hold", "icon": "🟡", "text": "觀望",
                "reason": f"{goods[0]}但{risks[0]}"}
    if goods:
        return {"level": "hold", "icon": "🟡", "text": "觀望",
                "reason": goods[0]}
    return {"level": "hold", "icon": "🟡", "text": "觀望", "reason": "無明確訊號"}


def bw_desc(rank: float) -> str:
    if rank <= 10:
        return "極窄"
    if rank <= 30:
        return "偏窄"
    if rank <= 70:
        return "中等"
    if rank <= 90:
        return "偏寬"
    return "極寬"


def pos_desc(pos: float) -> str:
    if pos >= 85:
        return "貼上軌"
    if pos >= 60:
        return "偏上"
    if pos >= 40:
        return "中間"
    if pos >= 15:
        return "偏下"
    return "貼下軌"


def cat_tag(a: dict) -> str:
    """〔類別〕，沒有分類資料時回空字串。"""
    c = a.get("category", "")
    return f"〔{c}〕" if c else ""


def action_hint(a: dict) -> str:
    bb = a.get("bb", {})
    rank = bb.get("rank", 9)
    res = a["ladder"]["resistance"]
    sup = a["ladder"]["support"]

    if rank in (3, 4):
        return (f"等表態：站上{bb['upper']:.1f}轉多／"
                f"跌破{bb['lower']:.1f}轉空")
    if rank in (0, 1):
        if sup:
            return f"追高需設停損{sup[0]['price']:.1f}"
        return "追高需嚴設停損"
    if rank == 2:
        return "爆量不漲，先觀察"
    if rank == 5:
        return "已破下軌，勿接刀"
    if res and sup:
        return f"區間 {sup[0]['price']:.1f}～{res[0]['price']:.1f}"
    return ""


def format_card(a: dict) -> str:
    lines = []
    bb = a.get("bb", {})
    flow = a.get("flow", {})
    margin = a.get("margin", {})
    res = a["ladder"]["resistance"]
    sup = a["ladder"]["support"]
    v = verdict(a)
    sig_icon = bb.get("icon", "") or ""

    lines.append(f"{v['icon']} {a['name']} {a['code']}{cat_tag(a)} "
                 f"{a['close']:.1f}（{a['chg']:+.1f}%）{sig_icon}")
    lines.append(f"  ▸ {v['text']}：{v['reason']}")

    hint = action_hint(a)
    if hint:
        lines.append(f"  ▸ {hint}")

    if a.get("thin_data"):
        lines.append(f"  ▸ ⚠️ 分位樣本僅{a.get('bw_window', 0)}筆"
                     f"（需{BW_WINDOW}），縮口判定可信度低")

    if flow:
        parts = []
        if flow["foreign"] != 0:
            if flow["foreign"] < 0 and flow["f_sell_streak"] >= 2:
                s = f"連賣{flow['f_sell_streak']}"
            elif flow["f_streak"] >= 2:
                s = f"連買{flow['f_streak']}"
            else:
                s = ""
            parts.append(f"外{flow['foreign']:+.0f}{s}")
        if flow["trust"] != 0:
            if flow["trust"] < 0 and flow["t_sell_streak"] >= 2:
                s = f"連賣{flow['t_sell_streak']}"
            elif flow["t_streak"] >= 2:
                s = f"連買{flow['t_streak']}"
            else:
                s = ""
            parts.append(f"投{flow['trust']:+.0f}{s}")
        lines.append(f"  籌碼 {' '.join(parts) if parts else '法人無動作'}")

    if bb:
        rank = bb.get("bw_rank", 50)
        pos = bb.get("position", 50)
        if bb.get("signal"):
            if bb["rank"] <= 1:
                sq = bb["squeeze_before"] or bb["squeeze_days"]
                tail = f" 壓縮{sq}日後" if sq else ""
                lines.append(f"  {bb['signal']} 量{bb['vol_ratio']:.1f}倍 "
                             f"K{bb['body']:+.1f}%{tail}")
            elif bb["rank"] in (2, 5):
                lines.append(f"  {bb['signal']} 量{bb['vol_ratio']:.1f}倍 "
                             f"K{bb['body']:+.1f}%")
            else:
                lines.append(f"  {bb['signal']}{bb['squeeze_days']}日 "
                             f"上{bb['upper']:.1f}/下{bb['lower']:.1f}")
        lines.append(f"  帶寬{bb.get('bw', 0):.1f}%（{bw_desc(rank)}·分位{rank:.0f}）"
                     f"｜位置{pos_desc(pos)}")

    if res:
        mark = "※" if res[0].get("estimated") else ""
        lines.append(f"  壓力 {res[0]['price']:.1f}（{res[0]['pct']:+.1f}%）{mark}")
    if sup:
        s2 = f" 破→{sup[1]['price']:.1f}" if len(sup) >= 2 else ""
        lines.append(f"  支撐 {sup[0]['price']:.1f}（{sup[0]['pct']:+.1f}%）{s2}")

    if a.get("rr") is not None:
        rv, cf = a["rr"], a["rr_conf"]
        ic = "❓" if cf == "low" else "✅" if rv >= 1.5 else "⚠️" if rv < 0.8 else "➖"
        lines.append(f"  盈虧比 1:{rv:.2f} {ic}")

    notes = []
    pnl = a.get("margin_pnl")
    if pnl is not None:
        if pnl > 15:
            notes.append(f"融資獲利{pnl:+.0f}%賣壓重")
        elif pnl < -5:
            notes.append(f"融資套牢{pnl:+.0f}%壓力釋放")
    rc = margin.get("recent_change")
    if rc is not None:
        if rc > 30:
            notes.append(f"融資{rc:+.0f}%慎入")
        elif rc > 10:
            notes.append(f"融資{rc:+.0f}%火藥累積")
        elif rc < -10:
            notes.append(f"融資{rc:+.0f}%籌碼沉澱")
    if notes:
        lines.append(f"  {'｜'.join(notes)}")

    return "\n".join(lines)


def _brief_line(a: dict, with_cat: bool = True) -> str:
    rr = a.get("rr")
    rr_s = f"1:{rr:.1f}" if rr is not None else "—"
    sig = a.get("bb", {}).get("icon", "")
    tag = cat_tag(a) if with_cat else ""
    thin = "⚠️" if a.get("thin_data") else ""
    return (f"  {a['name']}{a['code']}{tag} {a['close']:.1f} "
            f"{a['chg']:+.1f}% ⚖️{rr_s} {sig}{thin}")


def format_summary(results: list) -> str:
    """核心名單速覽：依三級分類，每行帶產業類別。"""
    groups = {"watch": [], "hold": [], "avoid": []}
    for a in results:
        groups[verdict(a)["level"]].append(a)

    lines = ["📋 核心名單速覽", "═" * 16]
    for key, title in [("watch", "🟢 可留意"), ("hold", "🟡 觀望"), ("avoid", "🔴 避開")]:
        items = groups[key]
        if not items:
            continue
        lines.append(f"{title}（{len(items)}）")
        for a in items:
            lines.append(_brief_line(a))
    return "\n".join(lines)


def format_theme_summary(results: list) -> str:
    """觀察名單速覽：依產業類別分組，每支一行。"""
    if not results:
        return ""
    by_code = {a["code"]: a for a in results}
    lines = ["🗂️ 觀察名單（依類別）", "═" * 16]
    for cat, codes in _by_category(list(by_code.keys())):
        members = [by_code[c] for c in codes if c in by_code]
        if not members:
            continue
        members.sort(key=lambda x: -x["score"])
        lines.append(f"【{cat or '未分類'}】")
        for a in members:
            v = verdict(a)
            rr = a.get("rr")
            rr_s = f"1:{rr:.1f}" if rr is not None else "—"
            sig = a.get("bb", {}).get("icon", "")
            thin = "⚠️" if a.get("thin_data") else ""
            lines.append(f"  {v['icon']}{a['name']}{a['code']} {a['close']:.1f} "
                         f"{a['chg']:+.1f}% ⚖️{rr_s} {sig}{thin}")
    return "\n".join(lines)


def format_report(results: list) -> str:
    """
    分層輸出：
      核心 -> 速覽 + 完整個股詳情
      觀察 -> 只出依類別分組的速覽
    這樣 36 支的訊息長度仍可壓在 LINE 單則 4800 字內。
    """
    core = core_results(results)
    watch = watch_results(results)

    lines = [format_summary(core), ""]
    lines.append("═" * 16)
    lines.append("📊 核心個股詳情")
    lines.append("")
    for a in core:
        lines.append(format_card(a))
        lines.append("")

    if watch:
        lines.append("═" * 16)
        lines.append(format_theme_summary(watch))
        lines.append("")
        lines.append("（觀察名單不進盤中監控，僅每日盤前更新）")

    return "\n".join(lines)


if __name__ == "__main__":
    print(f"開始分析：核心 {len(CORE)} 支 / 觀察 {len(WATCH)} 支"
          f"{'' if _HAS_CAT else '（未載入 categories.py，退回原名單）'}")
    rs = analyze_all()
    if rs:
        print(f"\n資料日期：{rs[0]['data_date']}（前一日 {rs[0]['prev_date']}）\n")
        report = format_report(rs)
        print(report)
        print(f"\n--- 訊息長度 {len(report)} 字"
              f"（LINE 單則上限 4800，超過會分段並多吃額度）---")
