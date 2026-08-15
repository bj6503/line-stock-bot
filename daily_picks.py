import requests
import datetime
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_INDEX = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_INST = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"

# 過濾門檻
MIN_ATR_PCT = 1.5        # 日均波動至少1.5%
MIN_RANGE_PCT = 12.0     # 20日振幅至少12%
MIN_AVG_VOLUME = 2000    # 20日均量至少2000張
MIN_FOREIGN = 300        # 外資買超門檻(張)
MIN_TRUST = 100          # 投信買超門檻(張)


def to_num(s) -> float:
    if s is None:
        return 0.0
    s = str(s).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "--", "---"):
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def get_twse_inst(date_str: str) -> dict:
    params = {"date": date_str, "selectType": "ALL", "response": "json"}
    try:
        r = requests.get(TWSE_T86, params=params, headers=HEADERS, timeout=20)
        j = r.json()
    except Exception:
        return {}
    if j.get("stat") != "OK":
        return {}

    fields = j.get("fields", [])
    idx = {}
    for i, f in enumerate(fields):
        if "證券代號" in f:
            idx["code"] = i
        elif "證券名稱" in f:
            idx["name"] = i
        elif "外陸資買賣超股數(不含外資自營商)" in f:
            idx["foreign"] = i
        elif "投信買賣超股數" in f:
            idx["trust"] = i
    if "foreign" not in idx:
        for i, f in enumerate(fields):
            if "外" in f and "買賣超" in f and "自營" not in f:
                idx["foreign"] = i
                break

    result = {}
    for row in j.get("data", []):
        try:
            code = str(row[idx["code"]]).strip()
            result[code] = {
                "name": str(row[idx.get("name", 1)]).strip(),
                "foreign": to_num(row[idx["foreign"]]) / 1000,
                "trust": to_num(row[idx["trust"]]) / 1000,
            }
        except Exception:
            continue
    return result


def get_tpex_inst(date_str: str) -> dict:
    roc = f"{int(date_str[:4]) - 1911}/{date_str[4:6]}/{date_str[6:]}"
    params = {"date": roc, "type": "Daily", "response": "json"}
    try:
        r = requests.get(TPEX_INST, params=params, headers=HEADERS, timeout=20)
        j = r.json()
    except Exception:
        return {}
    rows = j.get("aaData") or (j.get("tables", [{}])[0].get("data", []) if j.get("tables") else [])
    result = {}
    for row in rows:
        try:
            code = str(row[0]).strip()
            if not code:
                continue
            result[code] = {
                "name": str(row[1]).strip(),
                "foreign": to_num(row[10]) / 1000 if len(row) > 10 else 0,
                "trust": to_num(row[13]) / 1000 if len(row) > 13 else 0,
            }
        except Exception:
            continue
    return result


def get_twse_daily(date_str: str) -> dict:
    """當日全市場行情：收盤、最高、最低、成交量"""
    params = {"date": date_str, "type": "ALLBUT0999", "response": "json"}
    try:
        r = requests.get(TWSE_INDEX, params=params, headers=HEADERS, timeout=25)
        j = r.json()
    except Exception:
        return {}

    out = {}
    for t in j.get("tables", []):
        fields = t.get("fields", [])
        if not any("證券代號" in f for f in fields):
            continue
        gi = lambda kw: next((i for i, f in enumerate(fields) if kw in f), None)
        ci, vi, pi, hi, li = gi("證券代號"), gi("成交股數"), gi("收盤價"), gi("最高價"), gi("最低價")
        if ci is None:
            continue
        for row in t.get("data", []):
            try:
                code = str(row[ci]).strip()
                out[code] = {
                    "volume": to_num(row[vi]) / 1000 if vi is not None else 0,
                    "close": to_num(row[pi]) if pi is not None else 0,
                    "high": to_num(row[hi]) if hi is not None else 0,
                    "low": to_num(row[li]) if li is not None else 0,
                }
            except Exception:
                continue
    return out


def recent_weekdays(n: int) -> list:
    days, d = [], datetime.date.today()
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= datetime.timedelta(days=1)
    return list(reversed(days))


def is_etf(code: str) -> bool:
    return code.startswith("00")


def calc_stock_quality(hist: list) -> dict:
    """用歷史行情算波動與量能"""
    if len(hist) < 10:
        return {}
    closes = [h["close"] for h in hist if h["close"] > 0]
    highs = [h["high"] for h in hist if h["high"] > 0]
    lows = [h["low"] for h in hist if h["low"] > 0]
    vols = [h["volume"] for h in hist]
    if not closes or not highs or not lows:
        return {}

    cur = closes[-1]
    # ATR%：每日(高-低)/收盤 的平均
    ranges = [(highs[i] - lows[i]) / closes[i] * 100 for i in range(len(closes)) if closes[i] > 0]
    atr_pct = sum(ranges) / len(ranges) if ranges else 0
    # 振幅
    range_pct = (max(highs) - min(lows)) / cur * 100 if cur > 0 else 0
    avg_vol = sum(vols) / len(vols) if vols else 0

    return {
        "close": cur,
        "atr_pct": atr_pct,
        "range_pct": range_pct,
        "avg_volume": avg_vol,
        "pass": atr_pct >= MIN_ATR_PCT and range_pct >= MIN_RANGE_PCT and avg_vol >= MIN_AVG_VOLUME,
    }


def calc_stars(item: dict, both: bool = False) -> int:
    """訊號強度星等 1-5"""
    s = 0
    if item["streak"] >= 5:
        s += 2
    elif item["streak"] >= 3:
        s += 1

    if item["ratio"] >= 20:
        s += 2
    elif item["ratio"] >= 10:
        s += 1

    if item["total"] >= item["net"] * 3:
        s += 1

    if both:
        s += 2

    return max(1, min(5, s))


def get_daily_picks(lookback: int = 6, quality_days: int = 20) -> dict:
    print("收集三大法人資料...")
    all_days = recent_weekdays(lookback + 4)
    daily = {}
    for d in reversed(all_days):
        tw = get_twse_inst(d)
        if not tw:
            continue
        tp = get_tpex_inst(d)
        daily[d] = {**tw, **tp}
        print(f"  {d}: 共{len(daily[d])}筆")
        time.sleep(1.2)
        if len(daily) >= lookback:
            break
    if not daily:
        return {}

    days = sorted(daily.keys())
    last_day = days[-1]

    print(f"收集{quality_days}日行情（波動/量能過濾）...")
    q_days = recent_weekdays(quality_days + 8)
    market_hist = {}
    got = 0
    for d in reversed(q_days):
        m = get_twse_daily(d)
        if not m:
            continue
        for code, v in m.items():
            market_hist.setdefault(code, []).append(v)
        got += 1
        print(f"  {d}: {len(m)}筆")
        time.sleep(1.0)
        if got >= quality_days:
            break

    print("計算個股品質...")
    quality = {}
    for code, hist in market_hist.items():
        hist.reverse()
        q = calc_stock_quality(hist)
        if q:
            quality[code] = q
    passed = sum(1 for q in quality.values() if q["pass"])
    print(f"  {len(quality)}支中 {passed}支通過波動/量能門檻")

    # 彙整法人
    stats = {}
    for d in days:
        for code, v in daily[d].items():
            s = stats.setdefault(code, {"name": v["name"], "hist": {}, "code": code})
            s["hist"][d] = v

    def build(investor: str, min_net: float) -> list:
        out = []
        for code, s in stats.items():
            q = quality.get(code)
            if not q or not q["pass"]:
                continue
            last = s["hist"].get(last_day)
            if not last:
                continue
            net = last[investor]
            if net < min_net:
                continue

            streak = 0
            for d in reversed(days):
                h = s["hist"].get(d)
                if h and h[investor] > 0:
                    streak += 1
                else:
                    break

            total = sum(s["hist"][d][investor] for d in days if d in s["hist"])
            vol = q["avg_volume"]
            ratio = (net / vol * 100) if vol > 0 else 0
            score = streak * 2 + min(ratio, 30) + min(total / 500, 8)

            out.append({
                "code": code, "name": s["name"], "net": net,
                "total": total, "streak": streak, "ratio": ratio,
                "close": q["close"], "atr": q["atr_pct"],
                "range": q["range_pct"], "avg_volume": vol,
                "score": score, "etf": is_etf(code),
            })
        out.sort(key=lambda x: -x["score"])
        return out

    f_all = build("foreign", MIN_FOREIGN)
    t_all = build("trust", MIN_TRUST)

    f_codes = {x["code"] for x in f_all if not x["etf"]}
    t_codes = {x["code"] for x in t_all if not x["etf"]}
    both_codes = f_codes & t_codes

    both = []
    for code in both_codes:
        f = next(x for x in f_all if x["code"] == code)
        t = next(x for x in t_all if x["code"] == code)
        item = dict(f)
        item["trust_net"] = t["net"]
        item["streak"] = max(f["streak"], t["streak"])
        item["score"] = f["score"] + t["score"]
        item["stars"] = calc_stars(item, both=True)
        both.append(item)
    both.sort(key=lambda x: -x["score"])

    f_stocks = [x for x in f_all if not x["etf"] and x["code"] not in both_codes][:5]
    t_stocks = [x for x in t_all if not x["etf"] and x["code"] not in both_codes][:5]
    for x in f_stocks:
        x["stars"] = calc_stars(x)
    for x in t_stocks:
        x["stars"] = calc_stars(x)

    return {
        "date": last_day,
        "both": both[:3],
        "foreign": f_stocks,
        "trust": t_stocks,
        "etf": [x for x in f_all if x["etf"]][:3],
    }


def downside_table(close: float) -> str:
    return (f"   下檔試算 -8%:{close*0.92:.1f} "
            f"-14%:{close*0.86:.1f} -20%:{close*0.80:.1f}")


def format_picks(picks: dict) -> str:
    if not picks:
        return "無法取得法人資料"
    d = picks["date"]
    lines = [f"📋 {d[4:6]}/{d[6:]} 主力動向", "═" * 16]

    if picks.get("both"):
        lines.append("🔥 雙主力同買")
        for x in picks["both"]:
            lines.append(f"{'★' * x['stars']}{'☆' * (5 - x['stars'])} {x['name']} {x['code']}")
            lines.append(f"   外資+{x['net']:.0f} 投信+{x['trust_net']:.0f}張 連{x['streak']}日")
            lines.append(f"   現價{x['close']:.1f} 佔均量{x['ratio']:.1f}% 波動{x['atr']:.1f}%")
            lines.append(downside_table(x["close"]))
        lines.append("─" * 16)

    lines.append("🏦 外資買超")
    for x in picks["foreign"]:
        lines.append(f"{'★' * x['stars']}{'☆' * (5 - x['stars'])} {x['name']} {x['code']}")
        lines.append(f"   +{x['net']:.0f}張 連{x['streak']}日 佔均量{x['ratio']:.1f}%")
        lines.append(f"   現價{x['close']:.1f} 波動{x['atr']:.1f}% 振幅{x['range']:.0f}%")
    lines.append("─" * 16)

    lines.append("🎯 投信買超")
    for x in picks["trust"]:
        lines.append(f"{'★' * x['stars']}{'☆' * (5 - x['stars'])} {x['name']} {x['code']}")
        lines.append(f"   +{x['net']:.0f}張 連{x['streak']}日 佔均量{x['ratio']:.1f}%")
        lines.append(f"   現價{x['close']:.1f} 波動{x['atr']:.1f}% 振幅{x['range']:.0f}%")

    if picks.get("etf"):
        lines.append("─" * 16)
        lines.append("📊 ETF資金")
        for x in picks["etf"]:
            lines.append(f"  {x['name']} {x['code']} +{x['net']:.0f}張")

    return "\n".join(lines)


if __name__ == "__main__":
    p = get_daily_picks()
    print()
    print(format_picks(p))
