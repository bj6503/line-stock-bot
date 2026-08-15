import requests
import datetime
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

TWSE_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_PRICE = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_INST = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"


def to_num(s) -> float:
    if s is None:
        return 0.0
    s = str(s).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "--"):
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def get_twse_inst(date_str: str) -> dict:
    """證交所三大法人買賣超（上市）date_str: YYYYMMDD"""
    params = {"date": date_str, "selectType": "ALL", "response": "json"}
    try:
        r = requests.get(TWSE_T86, params=params, headers=HEADERS, timeout=20)
        j = r.json()
    except Exception as e:
        print(f"  TWSE 取得失敗: {e}")
        return {}

    if j.get("stat") != "OK":
        return {}

    fields = j.get("fields", [])
    data = j.get("data", [])
    if not data:
        return {}

    # 找欄位索引
    idx = {}
    for i, f in enumerate(fields):
        if "證券代號" in f:
            idx["code"] = i
        elif "證券名稱" in f:
            idx["name"] = i
        elif "外陸資買賣超股數(不含外資自營商)" in f or ("外" in f and "買賣超" in f and "自營" not in f):
            idx.setdefault("foreign", i)
        elif "投信買賣超股數" in f:
            idx["trust"] = i

    result = {}
    for row in data:
        try:
            code = str(row[idx["code"]]).strip()
            name = str(row[idx.get("name", 1)]).strip()
            foreign = to_num(row[idx["foreign"]]) / 1000
            trust = to_num(row[idx["trust"]]) / 1000
            result[code] = {"name": name, "foreign": foreign, "trust": trust}
        except Exception:
            continue
    return result


def get_tpex_inst(date_str: str) -> dict:
    """櫃買中心三大法人（上櫃）date_str: YYYYMMDD"""
    roc = f"{int(date_str[:4]) - 1911}/{date_str[4:6]}/{date_str[6:]}"
    params = {"date": roc, "type": "Daily", "response": "json"}
    try:
        r = requests.get(TPEX_INST, params=params, headers=HEADERS, timeout=20)
        j = r.json()
    except Exception:
        return {}

    rows = j.get("aaData") or j.get("tables", [{}])[0].get("data", [])
    result = {}
    for row in rows:
        try:
            code = str(row[0]).strip()
            name = str(row[1]).strip()
            foreign = to_num(row[10]) / 1000 if len(row) > 10 else 0
            trust = to_num(row[13]) / 1000 if len(row) > 13 else 0
            if code:
                result[code] = {"name": name, "foreign": foreign, "trust": trust}
        except Exception:
            continue
    return result


def get_twse_volume(date_str: str) -> dict:
    """證交所當日成交量"""
    params = {"date": date_str, "type": "ALLBUT0999", "response": "json"}
    try:
        r = requests.get(TWSE_PRICE, params=params, headers=HEADERS, timeout=20)
        j = r.json()
    except Exception:
        return {}

    vol = {}
    tables = j.get("tables", [])
    for t in tables:
        fields = t.get("fields", [])
        if not any("證券代號" in f for f in fields):
            continue
        ci = next((i for i, f in enumerate(fields) if "證券代號" in f), None)
        vi = next((i for i, f in enumerate(fields) if "成交股數" in f), None)
        pi = next((i for i, f in enumerate(fields) if "收盤價" in f), None)
        if ci is None or vi is None:
            continue
        for row in t.get("data", []):
            try:
                code = str(row[ci]).strip()
                vol[code] = {
                    "volume": to_num(row[vi]) / 1000,
                    "close": to_num(row[pi]) if pi is not None else 0,
                }
            except Exception:
                continue
    return vol


def recent_weekdays(n: int = 8) -> list:
    """往回抓n個工作日（YYYYMMDD）"""
    days = []
    d = datetime.date.today()
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= datetime.timedelta(days=1)
    return list(reversed(days))


def is_etf(code: str) -> bool:
    return code.startswith("00")


def get_daily_picks(lookback: int = 6) -> dict:
    print("收集證交所三大法人資料...")
    all_days = recent_weekdays(lookback + 3)
    daily = {}

    for d in reversed(all_days):
        tw = get_twse_inst(d)
        if not tw:
            continue
        tp = get_tpex_inst(d)
        merged = {**tw, **tp}
        daily[d] = merged
        print(f"  {d}: 上市{len(tw)} 上櫃{len(tp)} 共{len(merged)}筆")
        time.sleep(1.2)
        if len(daily) >= lookback:
            break

    if not daily:
        return {}

    days = sorted(daily.keys())
    last_day = days[-1]

    print("取得成交量...")
    vols = get_twse_volume(last_day)
    time.sleep(1)

    # 彙整每支股票
    stats = {}
    for d in days:
        for code, v in daily[d].items():
            s = stats.setdefault(code, {
                "name": v["name"], "hist": {}, "code": code
            })
            s["hist"][d] = v

    def build(investor: str) -> list:
        out = []
        for code, s in stats.items():
            last = s["hist"].get(last_day)
            if not last:
                continue
            net = last[investor]
            if net <= 0:
                continue

            streak = 0
            for d in reversed(days):
                h = s["hist"].get(d)
                if h and h[investor] > 0:
                    streak += 1
                else:
                    break

            total = sum(s["hist"][d][investor] for d in days if d in s["hist"])
            vinfo = vols.get(code, {})
            volume = vinfo.get("volume", 0)
            ratio = (net / volume * 100) if volume > 0 else 0

            score = streak * 2 + min(ratio, 30) + min(total / 500, 10)

            out.append({
                "code": code, "name": s["name"],
                "net": net, "total": total, "streak": streak,
                "ratio": ratio, "volume": volume,
                "close": vinfo.get("close", 0),
                "score": score, "etf": is_etf(code),
            })
        out.sort(key=lambda x: -x["score"])
        return out

    f_all = build("foreign")
    t_all = build("trust")

    f_stocks = [x for x in f_all if not x["etf"]][:5]
    t_stocks = [x for x in t_all if not x["etf"]][:5]
    etfs = [x for x in f_all if x["etf"]][:3]

    f_top = {x["code"] for x in f_all[:50] if not x["etf"]}
    t_top = {x["code"] for x in t_all[:50] if not x["etf"]}
    both = []
    for code in f_top & t_top:
        f = next(x for x in f_all if x["code"] == code)
        t = next(x for x in t_all if x["code"] == code)
        both.append({
            "code": code, "name": f["name"],
            "foreign": f["net"], "trust": t["net"],
            "streak": max(f["streak"], t["streak"]),
            "score": f["score"] + t["score"],
        })
    both.sort(key=lambda x: -x["score"])

    return {
        "date": last_day,
        "foreign": f_stocks,
        "trust": t_stocks,
        "etf": etfs,
        "both": both[:3],
    }


def format_picks(picks: dict) -> str:
    if not picks:
        return "無法取得法人資料"

    d = picks["date"]
    date_fmt = f"{d[4:6]}/{d[6:]}"
    lines = [f"📋 {date_fmt} 主力動向", "═" * 16]

    if picks.get("both"):
        lines.append("🔥 雙主力同買")
        for b in picks["both"]:
            lines.append(f"  {b['name']} {b['code']}")
            lines.append(f"  外資{b['foreign']:+.0f} 投信{b['trust']:+.0f}張 連{b['streak']}日")
        lines.append("─" * 16)

    lines.append("🏦 外資買超前5")
    for i, x in enumerate(picks["foreign"], 1):
        lines.append(f"{i}. {x['name']} {x['code']}")
        lines.append(f"   +{x['net']:.0f}張 連{x['streak']}日 佔量{x['ratio']:.1f}%")
    lines.append("─" * 16)

    lines.append("🎯 投信買超前5")
    for i, x in enumerate(picks["trust"], 1):
        lines.append(f"{i}. {x['name']} {x['code']}")
        lines.append(f"   +{x['net']:.0f}張 連{x['streak']}日 佔量{x['ratio']:.1f}%")

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
