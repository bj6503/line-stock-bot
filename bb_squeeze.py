import requests
import datetime
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}
TWSE_INDEX = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"

BB_PERIOD = 20
BB_STD = 2.0
SQUEEZE_PCT = 25

VOLUME_MULTIPLE = 2.0      # 噴出量增門檻
VOLUME_WARN = 1.5          # 準噴出量增門檻
MIN_BODY_PCT = 2.0         # 噴出最低漲幅
DIVERGE_VOL = 3.0          # 量增超過此倍數但漲幅不足 → 量價背離

SQUEEZE_RECENT = 5
BREAK_LOOKBACK = 10

HISTORY_DAYS = 70
MIN_PRICE = 30.0
MIN_AVG_VOLUME = 1000

FINANCE_EXTRA = {"5880"}


def is_finance(code: str) -> bool:
    return code in FINANCE_EXTRA or (len(code) == 4 and code.startswith("28"))


def is_etf(code: str) -> bool:
    return code.startswith("00")


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
        ci, ni = gi("證券代號"), gi("證券名稱")
        vi, oi = gi("成交股數"), gi("開盤價")
        pi, hi, li = gi("收盤價"), gi("最高價"), gi("最低價")
        if ci is None or pi is None:
            continue
        for row in t.get("data", []):
            try:
                code = str(row[ci]).strip()
                close = to_num(row[pi])
                if close <= 0:
                    continue
                out[code] = {
                    "name": str(row[ni]).strip() if ni is not None else code,
                    "open": to_num(row[oi]) if oi is not None else close,
                    "close": close,
                    "high": to_num(row[hi]) if hi is not None else close,
                    "low": to_num(row[li]) if li is not None else close,
                    "volume": to_num(row[vi]) / 1000 if vi is not None else 0,
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


def calc_bb(closes: list) -> dict:
    if len(closes) < BB_PERIOD:
        return {}
    w = closes[-BB_PERIOD:]
    mid = sum(w) / BB_PERIOD
    std = (sum((c - mid) ** 2 for c in w) / BB_PERIOD) ** 0.5
    upper, lower = mid + BB_STD * std, mid - BB_STD * std
    return {"upper": upper, "mid": mid, "lower": lower,
            "bw": (upper - lower) / mid * 100 if mid > 0 else 0}


def percentile(values: list, p: float) -> float:
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def count_squeeze_before(bws: list, threshold: float, end_index: int) -> int:
    """計算 end_index 之前（不含）的連續縮口天數"""
    days = 0
    for i in range(end_index - 1, -1, -1):
        if bws[i] <= threshold:
            days += 1
        else:
            break
    return days


def analyze(code: str, hist: list) -> dict:
    if len(hist) < BB_PERIOD + 15:
        return {}

    closes = [h["close"] for h in hist]
    bws = []
    for i in range(BB_PERIOD, len(closes) + 1):
        bb = calc_bb(closes[:i])
        if bb:
            bws.append(bb["bw"])
    if len(bws) < 15:
        return {}

    cur_bb = calc_bb(closes)
    cur_bw = cur_bb["bw"]
    threshold = percentile(bws, SQUEEZE_PCT)
    in_squeeze = cur_bw <= threshold

    # 今日起算的連續縮口天數
    squeeze_days = 0
    for b in reversed(bws):
        if b <= threshold:
            squeeze_days += 1
        else:
            break

    # 噴出前的縮口天數（從最後一筆之前往回算）
    squeeze_before = count_squeeze_before(bws, threshold, len(bws) - 1)

    recent = bws[-(SQUEEZE_RECENT + 1):-1] if len(bws) > SQUEEZE_RECENT else bws[:-1]
    was_squeezed = in_squeeze or any(b <= threshold for b in recent)

    today, prev = hist[-1], hist[-2]
    red_k = today["close"] > today["open"]
    body = (today["close"] - today["open"]) / today["open"] * 100 if today["open"] > 0 else 0
    vol_ratio = (today["volume"] / prev["volume"]) if prev["volume"] > 0 else 0

    prior = [h["high"] for h in hist[-(BREAK_LOOKBACK + 1):-1]]
    break_high = today["close"] > max(prior) if prior else False
    break_upper = today["close"] > cur_bb["upper"]
    break_up = break_high or break_upper

    bw_rank = sum(1 for b in bws if b < cur_bw) / len(bws) * 100
    avg_vol = sum(h["volume"] for h in hist[-20:]) / min(20, len(hist))

    # 量價背離：爆量但漲幅不足
    diverge = vol_ratio >= DIVERGE_VOL and body < MIN_BODY_PCT

    # 訊號判定（噴出必須紅K且漲幅達標）
    strong = red_k and body >= MIN_BODY_PCT
    if was_squeezed and strong and vol_ratio >= VOLUME_MULTIPLE and break_up:
        signal, icon, rank = "縮口噴出", "🚀", 0
    elif was_squeezed and strong and vol_ratio >= VOLUME_WARN and break_up:
        signal, icon, rank = "準噴出", "⚡", 1
    elif diverge and was_squeezed:
        signal, icon, rank = "量價背離", "⚠️", 2
    elif in_squeeze and squeeze_days >= 8:
        signal, icon, rank = "極度縮口", "🔵", 3
    elif in_squeeze and squeeze_days >= 4:
        signal, icon, rank = "縮口中", "🔹", 4
    else:
        return {}

    return {
        "code": code, "close": today["close"], "bw": cur_bw,
        "bw_rank": bw_rank,
        "squeeze_days": squeeze_days,
        "squeeze_before": squeeze_before,
        "body": body, "vol_ratio": vol_ratio, "avg_volume": avg_vol,
        "break_high": break_high, "break_upper": break_upper,
        "upper": cur_bb["upper"], "mid": cur_bb["mid"], "lower": cur_bb["lower"],
        "signal": signal, "icon": icon, "rank": rank, "diverge": diverge,
    }


def scan_market(max_days: int = HISTORY_DAYS) -> dict:
    print(f"收集{max_days}日全市場行情...")
    hist_map, names, got = {}, {}, 0
    for d in reversed(recent_weekdays(max_days + 15)):
        m = get_twse_daily(d)
        if not m:
            continue
        for code, v in m.items():
            hist_map.setdefault(code, []).append(v)
            names[code] = v["name"]
        got += 1
        if got % 10 == 0:
            print(f"  已取得 {got} 日")
        time.sleep(0.9)
        if got >= max_days:
            break
    print(f"  完成，共 {got} 日 / {len(hist_map)} 支")

    print("分析布林通道...")
    results = []
    for code, hist in hist_map.items():
        if is_finance(code) or is_etf(code) or len(code) != 4:
            continue
        hist.reverse()
        if len(hist) < BB_PERIOD + 15:
            continue
        if hist[-1]["close"] < MIN_PRICE:
            continue
        avg_vol = sum(h["volume"] for h in hist[-20:]) / min(20, len(hist))
        if avg_vol < MIN_AVG_VOLUME:
            continue
        r = analyze(code, hist)
        if r:
            r["name"] = names.get(code, code)
            results.append(r)

    results.sort(key=lambda x: (x["rank"], x["bw_rank"], -x["vol_ratio"]))

    return {
        "burst": [r for r in results if r["rank"] <= 1][:8],
        "diverge": [r for r in results if r["rank"] == 2][:4],
        "squeeze": [r for r in results if r["rank"] == 3][:8],
        "watching": [r for r in results if r["rank"] == 4][:5],
        "all": results,
    }


def format_burst(r: dict) -> str:
    lines = [f"{r['icon']} {r['name']} {r['code']}  {r['close']:.1f}"]
    lines.append(f"   紅K+{r['body']:.1f}% 量增{r['vol_ratio']:.1f}倍")
    parts = []
    if r["break_upper"]:
        parts.append("破上軌")
    if r["break_high"]:
        parts.append(f"破{BREAK_LOOKBACK}日高")
    sq = r["squeeze_before"] if r["squeeze_before"] > 0 else r["squeeze_days"]
    tail = f"｜壓縮{sq}日後發動" if sq > 0 else "｜剛脫離壓縮"
    lines.append(f"   {' + '.join(parts)}{tail}")
    return "\n".join(lines)


def format_squeeze(r: dict) -> str:
    lines = [f"{r['icon']} {r['name']} {r['code']}  {r['close']:.1f}"]
    lines.append(f"   縮口{r['squeeze_days']}日 帶寬{r['bw']:.1f}%（分位{r['bw_rank']:.0f}）")
    lines.append(f"   上軌{r['upper']:.1f} 下軌{r['lower']:.1f}")
    return "\n".join(lines)


def format_report(data: dict) -> str:
    if not data:
        return "掃描失敗"
    lines = ["🎯 布林通道掃描", "═" * 16]

    if data["burst"]:
        lines.append("🚀 今日噴出")
        for r in data["burst"]:
            lines.append(format_burst(r))
        lines.append("─" * 16)

    if data["diverge"]:
        lines.append("⚠️ 量價背離（爆量不漲，慎入）")
        for r in data["diverge"]:
            lines.append(f"  {r['name']} {r['code']} 量增{r['vol_ratio']:.1f}倍 但僅{r['body']:+.1f}%")
        lines.append("─" * 16)

    if data["squeeze"]:
        lines.append("🔵 極度縮口（蓄勢待發）")
        for r in data["squeeze"]:
            lines.append(format_squeeze(r))
        lines.append("─" * 16)

    if data["watching"]:
        lines.append("🔹 縮口觀察")
        for r in data["watching"]:
            lines.append(f"  {r['name']} {r['code']} 縮口{r['squeeze_days']}日 分位{r['bw_rank']:.0f}")

    if not any([data["burst"], data["diverge"], data["squeeze"], data["watching"]]):
        lines.append("目前無訊號")

    return "\n".join(lines)


def find_golden(bb_data: dict, picks: dict) -> list:
    if not bb_data or not picks:
        return []
    bb_map = {r["code"]: r for r in bb_data.get("all", [])}
    golden, seen = [], set()
    groups = [("雙主力", picks.get("both", [])),
              ("投信", picks.get("trust", [])),
              ("外資", picks.get("foreign", []))]
    for label, items in groups:
        for x in items:
            code = x["code"]
            if code in seen or code not in bb_map:
                continue
            r = bb_map[code]
            if r["rank"] == 2 or r["rank"] > 3:
                continue
            seen.add(code)
            golden.append({"code": code, "name": x["name"],
                           "source": label, "bb": r, "picks": x})
    order = {"雙主力": 0, "投信": 1, "外資": 2}
    golden.sort(key=lambda g: (g["bb"]["rank"], order.get(g["source"], 9)))
    return golden


def format_golden(golden: list) -> str:
    if not golden:
        return ""
    lines = ["💎 黃金組合（主力買+布林訊號）", "─" * 16]
    for g in golden:
        bb, p = g["bb"], g["picks"]
        lines.append(f"{bb['icon']} {g['name']} {g['code']}  {bb['close']:.1f}")
        lines.append(f"   {g['source']}買超 連{p.get('streak', 0)}日｜{bb['signal']}")
        if bb["rank"] <= 1:
            sq = bb["squeeze_before"] if bb["squeeze_before"] > 0 else bb["squeeze_days"]
            lines.append(f"   量增{bb['vol_ratio']:.1f}倍 紅K+{bb['body']:.1f}% 壓縮{sq}日")
        else:
            lines.append(f"   縮口{bb['squeeze_days']}日 分位{bb['bw_rank']:.0f}")
    return "\n".join(lines)


if __name__ == "__main__":
    data = scan_market()
    print()
    print(format_report(data))
    print()
    print("─" * 40)
    print(f"總計 {len(data['all'])} 支有訊號")
