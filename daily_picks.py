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

MIN_ATR_PCT = 2.0
MIN_RANGE_PCT = 15.0
MIN_AVG_VOLUME = 2000
MIN_PRICE = 50.0
MIN_FOREIGN = 300
MIN_TRUST = 100

MAX_REASONABLE_NET = 100000   # 單日買超上限(張)，超過視為資料異常

FINANCE_EXTRA = {"5880"}


def is_finance(code: str) -> bool:
    return code in FINANCE_EXTRA or (len(code) == 4 and code.startswith("28"))


def clean_name(name: str) -> str:
    """清掉證交所特殊註記符號"""
    return str(name).replace("*", "").replace("＊", "").strip()


def to_num(s) -> float:
    if s is None:
        return 0.0
    s = str(s).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "--", "---", "X"):
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def get_twse_inst(date_str: str) -> dict:
    params = {"date": date_str, "selectType": "ALL", "response": "json"}
    try:
        j = requests.get(TWSE_T86, params=params, headers=HEADERS, timeout=20).json()
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
                "name": clean_name(row[idx.get("name", 1)]),
                "foreign": to_num(row[idx["foreign"]]) / 1000,
                "trust": to_num(row[idx["trust"]]) / 1000,
            }
        except Exception:
            continue
    return result


def get_tpex_inst(date_str: str) -> dict:
    """櫃買中心：用欄位名稱定位，不寫死索引"""
    roc = f"{int(date_str[:4]) - 1911}/{date_str[4:6]}/{date_str[6:]}"
    params = {"date": roc, "type": "Daily", "response": "json"}
    try:
        j = requests.get(TPEX_INST, params=params, headers=HEADERS, timeout=20).json()
    except Exception:
        return {}

    rows, fields = [], []
    if j.get("tables"):
        t = j["tables"][0]
        rows = t.get("data", [])
        fields = t.get("fields", [])
    elif j.get("aaData"):
        rows = j["aaData"]

    fi = ti = None
    if fields:
        for i, f in enumerate(fields):
            fs = str(f)
            if fi is None and "外資及陸資" in fs and "不含外資自營商" in fs and "買賣超" in fs:
                fi = i
            elif fi is None and "外資" in fs and "買賣超" in fs and "自營" not in fs:
                fi = i
            if ti is None and "投信" in fs and "買賣超" in fs:
                ti = i

    result = {}
    for row in rows:
        try:
            code = str(row[0]).strip()
            if not code or len(code) != 4:
                continue
            f_val = to_num(row[fi]) if fi is not None and len(row) > fi else 0
            t_val = to_num(row[ti]) if ti is not None and len(row) > ti else 0
            foreign, trust = f_val / 1000, t_val / 1000
            # 異常值過濾
            if abs(foreign) > MAX_REASONABLE_NET or abs(trust) > MAX_REASONABLE_NET:
                continue
            result[code] = {
                "name": clean_name(row[1]),
                "foreign": foreign,
                "trust": trust,
            }
        except Exception:
            continue
    return result


def get_twse_daily(date_str: str) -> dict:
    params = {"date": date_str, "type": "ALLBUT0999", "response": "json"}
    try:
        j = requests.get(TWSE_INDEX, params=params, headers=HEADERS, timeout=25).json()
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


def calc_quality(hist: list) -> dict:
    if len(hist) < 10:
        return {}
    closes = [h["close"] for h in hist if h["close"] > 0]
    highs = [h["high"] for h in hist if h["high"] > 0]
    lows = [h["low"] for h in hist if h["low"] > 0]
    vols = [h["volume"] for h in hist]
    if not closes or not highs or not lows:
        return {}
    cur = closes[-1]
    n = min(len(closes), len(highs), len(lows))
    ranges = [(highs[i] - lows[i]) / closes[i] * 100 for i in range(n) if closes[i] > 0]
    atr = sum(ranges) / len(ranges) if ranges else 0
    rng = (max(highs) - min(lows)) / cur * 100 if cur > 0 else 0
    avg_vol = sum(vols) / len(vols) if vols else 0
    return {
        "close": cur, "atr_pct": atr, "range_pct": rng,
        "avg_volume": avg_vol, "last_volume": vols[-1] if vols else 0,
        "pass": (atr >= MIN_ATR_PCT and rng >= MIN_RANGE_PCT
                 and avg_vol >= MIN_AVG_VOLUME and cur >= MIN_PRICE),
    }


def calc_stars(x: dict, both: bool = False) -> int:
    s = 0
    if x["streak"] >= 5:
        s += 2
    elif x["streak"] >= 3:
        s += 1
    if x["ratio"] >= 20:
        s += 2
    elif x["ratio"] >= 10:
        s += 1
    if x["atr"] >= 5:
        s += 2
    elif x["atr"] >= 3.5:
        s += 1
    if x["range"] >= 30:
        s += 1
    if both:
        s += 2
    return max(1, min(5, s))


def get_daily_picks(lookback: int = 6, quality_days: int = 20) -> dict:
    print("收集三大法人資料...")
    daily = {}
    for d in reversed(recent_weekdays(lookback + 4)):
        tw = get_twse_inst(d)
        if not tw:
            continue
        daily[d] = {**tw, **get_tpex_inst(d)}
        print(f"  {d}: {len(daily[d])}筆")
        time.sleep(1.2)
        if len(daily) >= lookback:
            break
    if not daily:
        return {}

    days = sorted(daily.keys())
    last_day = days[-1]

    print(f"收集{quality_days}日行情...")
    hist_map, got = {}, 0
    for d in reversed(recent_weekdays(quality_days + 8)):
        m = get_twse_daily(d)
        if not m:
            continue
        for code, v in m.items():
            hist_map.setdefault(code, []).append(v)
        got += 1
        time.sleep(1.0)
        if got >= quality_days:
            break
    print(f"  取得 {got} 日")

    quality = {}
    for code, h in hist_map.items():
        h.reverse()
        q = calc_quality(h)
        if q:
            quality[code] = q
    print(f"  {len(quality)}支中 {sum(1 for q in quality.values() if q['pass'])}支通過門檻")

    stats = {}
    for d in days:
        for code, v in daily[d].items():
            stats.setdefault(code, {"name": v["name"], "hist": {}, "code": code})["hist"][d] = v

    def build(inv: str, min_net: float) -> list:
        out = []
        for code, s in stats.items():
            if is_finance(code) or code.startswith("00"):
                continue
            q = quality.get(code)
            if not q or not q["pass"]:
                continue
            last = s["hist"].get(last_day)
            if not last or last[inv] < min_net:
                continue
            net = last[inv]
            if net > MAX_REASONABLE_NET:
                continue

            streak = 0
            for d in reversed(days):
                h = s["hist"].get(d)
                if h and h[inv] > 0:
                    streak += 1
                else:
                    break

            total = sum(s["hist"][d][inv] for d in days if d in s["hist"])
            vol = q["last_volume"] if q["last_volume"] > 0 else q["avg_volume"]
            ratio = min((net / vol * 100) if vol > 0 else 0, 100)

            item = {
                "code": code, "name": s["name"], "net": net, "total": total,
                "streak": streak, "ratio": ratio, "close": q["close"],
                "atr": q["atr_pct"], "range": q["range_pct"],
                "avg_volume": q["avg_volume"],
            }
            item["score"] = streak * 2 + min(ratio, 25) + q["atr_pct"] * 2 + min(total / 500, 8)
            out.append(item)
        out.sort(key=lambda x: -x["score"])
        return out

    f_all = build("foreign", MIN_FOREIGN)
    t_all = build("trust", MIN_TRUST)

    f_codes = {x["code"] for x in f_all}
    t_codes = {x["code"] for x in t_all}
    both_codes = f_codes & t_codes

    both = []
    for code in both_codes:
        f = next(x for x in f_all if x["code"] == code)
        t = next(x for x in t_all if x["code"] == code)
        it = dict(f)
        it["trust_net"] = t["net"]
        it["streak"] = max(f["streak"], t["streak"])
        it["score"] = f["score"] + t["score"]
        it["stars"] = calc_stars(it, both=True)
        both.append(it)
    both.sort(key=lambda x: -x["score"])

    f_stocks = [x for x in f_all if x["code"] not in both_codes][:5]
    t_stocks = [x for x in t_all if x["code"] not in both_codes][:5]
    for x in f_stocks + t_stocks:
        x["stars"] = calc_stars(x)

    # 擴充池（給黃金組合比對用）
    f_pool = [x for x in f_all][:25]
    t_pool = [x for x in t_all][:25]
    for x in f_pool + t_pool:
        if "stars" not in x:
            x["stars"] = calc_stars(x)

    etfs = []
    for code, s in stats.items():
        if code.startswith("00"):
            last = s["hist"].get(last_day)
            if last and last["foreign"] > 0:
                etfs.append({"code": code, "name": s["name"], "net": last["foreign"]})
    etfs.sort(key=lambda x: -x["net"])

    return {
        "date": last_day, "both": both[:3],
        "foreign": f_stocks, "trust": t_stocks, "etf": etfs[:3],
        "foreign_pool": f_pool, "trust_pool": t_pool,
    }


def stars_str(n: int) -> str:
    return "★" * n + "☆" * (5 - n)


def format_picks(picks: dict) -> str:
    if not picks:
        return "無法取得法人資料"
    d = picks["date"]
    lines = [f"📋 {d[4:6]}/{d[6:]} 主力動向", "═" * 16]

    if picks.get("both"):
        lines.append("🔥 雙主力同買")
        for x in picks["both"]:
            lines.append(f"{stars_str(x['stars'])} {x['name']} {x['code']}")
            lines.append(f"   外資+{x['net']:.0f} 投信+{x['trust_net']:.0f}張 連{x['streak']}日")
            lines.append(f"   現價{x['close']:.1f} 佔量{x['ratio']:.0f}% 波動{x['atr']:.1f}%")
        lines.append("─" * 16)

    lines.append("🎯 投信買超")
    for x in picks["trust"]:
        lines.append(f"{stars_str(x['stars'])} {x['name']} {x['code']}")
        lines.append(f"   +{x['net']:.0f}張 連{x['streak']}日 佔量{x['ratio']:.0f}%")
        lines.append(f"   現價{x['close']:.1f} 波動{x['atr']:.1f}% 振幅{x['range']:.0f}%")
    lines.append("─" * 16)

    lines.append("🏦 外資買超")
    for x in picks["foreign"]:
        lines.append(f"{stars_str(x['stars'])} {x['name']} {x['code']}")
        lines.append(f"   +{x['net']:.0f}張 連{x['streak']}日 佔量{x['ratio']:.0f}%")
        lines.append(f"   現價{x['close']:.1f} 波動{x['atr']:.1f}% 振幅{x['range']:.0f}%")

    if picks.get("etf"):
        lines.append("─" * 16)
        lines.append("📊 ETF資金")
        for x in picks["etf"]:
            lines.append(f"  {x['name']} {x['code']} +{x['net']:.0f}張")

    return "\n".join(lines)


if __name__ == "__main__":
    print(format_picks(get_daily_picks()))
