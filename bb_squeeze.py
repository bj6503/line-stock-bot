import os
import requests
import datetime

TOKEN = os.environ.get("FINMIND_TOKEN", "")
API_URL = "https://api.finmindtrade.com/api/v4/data"

BB_PERIOD = 20              # 布林週期
BB_STD = 2.0                # 標準差倍數
LOOKBACK_BW = 120           # 帶寬百分位的回看天數
SQUEEZE_PCT = 25            # 帶寬落在後25%視為縮口
VOLUME_MULTIPLE = 2.0       # 噴出要量增2倍
VOLUME_WARN = 1.5           # 準噴出門檻
SQUEEZE_RECENT = 5          # 近5日內曾縮口才算有效噴出
BREAK_LOOKBACK = 10         # 突破近10日高


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


def get_history(stock_code: str, days: int = 300) -> list:
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for p in fm_query("TaiwanStockPrice", stock_code, start):
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


def calc_bb(closes: list) -> dict:
    if len(closes) < BB_PERIOD:
        return {}
    w = closes[-BB_PERIOD:]
    mid = sum(w) / BB_PERIOD
    var = sum((c - mid) ** 2 for c in w) / BB_PERIOD
    std = var ** 0.5
    upper = mid + BB_STD * std
    lower = mid - BB_STD * std
    bw = (upper - lower) / mid * 100 if mid > 0 else 0
    return {"upper": upper, "mid": mid, "lower": lower, "bw": bw}


def bb_series(hist: list) -> list:
    """回傳每日帶寬序列，對齊 hist[BB_PERIOD-1:]"""
    closes = [h["close"] for h in hist]
    out = []
    for i in range(BB_PERIOD, len(closes) + 1):
        bb = calc_bb(closes[:i])
        if bb:
            out.append(bb)
    return out


def percentile(values: list, p: float) -> float:
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def analyze_squeeze(stock_code: str, stock_name: str = "") -> dict:
    hist = get_history(stock_code)
    if len(hist) < BB_PERIOD + 40:
        return {}

    bbs = bb_series(hist)
    if len(bbs) < 30:
        return {}

    bws = [b["bw"] for b in bbs]
    window = bws[-LOOKBACK_BW:] if len(bws) >= LOOKBACK_BW else bws
    threshold = percentile(window, SQUEEZE_PCT)

    cur_bb = bbs[-1]
    cur_bw = cur_bb["bw"]
    in_squeeze = cur_bw <= threshold

    # 連續縮口天數
    squeeze_days = 0
    for b in reversed(bws):
        if b <= threshold:
            squeeze_days += 1
        else:
            break

    # 近期是否曾縮口
    recent = bws[-(SQUEEZE_RECENT + 1):-1] if len(bws) > SQUEEZE_RECENT else bws[:-1]
    was_squeezed = any(b <= threshold for b in recent) or in_squeeze

    today = hist[-1]
    prev = hist[-2]

    red_k = today["close"] > today["open"]
    body_pct = (today["close"] - today["open"]) / today["open"] * 100 if today["open"] > 0 else 0
    vol_ratio = (today["volume"] / prev["volume"]) if prev["volume"] > 0 else 0

    prior_highs = [h["high"] for h in hist[-(BREAK_LOOKBACK + 1):-1]]
    break_high = today["close"] > max(prior_highs) if prior_highs else False
    break_upper = today["close"] > cur_bb["upper"]
    break_up = break_high or break_upper

    # 帶寬相對位置（0=史上最窄）
    bw_rank = sum(1 for b in window if b < cur_bw) / len(window) * 100 if window else 0

    # 訊號判定
    if was_squeezed and red_k and vol_ratio >= VOLUME_MULTIPLE and break_up:
        signal, icon = "縮口噴出", "🚀"
    elif was_squeezed and red_k and vol_ratio >= VOLUME_WARN and break_up:
        signal, icon = "準噴出", "⚡"
    elif in_squeeze and squeeze_days >= 5:
        signal, icon = "極度縮口", "🔵"
    elif in_squeeze:
        signal, icon = "縮口中", "🔹"
    else:
        signal, icon = "", ""

    return {
        "code": stock_code,
        "name": stock_name or stock_code,
        "close": today["close"],
        "bw": cur_bw,
        "bw_rank": bw_rank,
        "threshold": threshold,
        "in_squeeze": in_squeeze,
        "squeeze_days": squeeze_days,
        "red_k": red_k,
        "body_pct": body_pct,
        "vol_ratio": vol_ratio,
        "volume": today["volume"],
        "break_high": break_high,
        "break_upper": break_upper,
        "upper": cur_bb["upper"],
        "mid": cur_bb["mid"],
        "lower": cur_bb["lower"],
        "signal": signal,
        "icon": icon,
        "is_alert": signal in ("縮口噴出", "準噴出", "極度縮口"),
    }


def format_squeeze(s: dict) -> str:
    if not s or not s.get("signal"):
        return ""

    lines = [f"{s['icon']} {s['name']} {s['code']}  {s['signal']}"]

    if s["signal"] in ("縮口噴出", "準噴出"):
        lines.append(f"   紅K +{s['body_pct']:.1f}% 量增{s['vol_ratio']:.1f}倍")
        parts = []
        if s["break_upper"]:
            parts.append("突破上軌")
        if s["break_high"]:
            parts.append(f"破{BREAK_LOOKBACK}日高")
        if parts:
            lines.append(f"   {' + '.join(parts)}")
        lines.append(f"   縮口{s['squeeze_days']}日後發動")
        lines.append(f"   上軌{s['upper']:.1f} 中軌{s['mid']:.1f}")
    else:
        lines.append(f"   帶寬{s['bw']:.1f}%（近120日{s['bw_rank']:.0f}分位）")
        lines.append(f"   已縮口{s['squeeze_days']}日，待變盤")
        lines.append(f"   上軌{s['upper']:.1f} 下軌{s['lower']:.1f}")

    return "\n".join(lines)


def scan_list(items: list) -> list:
    """items: [(code, name), ...]"""
    results = []
    for code, name in items:
        s = analyze_squeeze(code, name)
        if s and s.get("signal"):
            results.append(s)
    order = {"縮口噴出": 0, "準噴出": 1, "極度縮口": 2, "縮口中": 3}
    results.sort(key=lambda x: (order.get(x["signal"], 9), -x["vol_ratio"]))
    return results


if __name__ == "__main__":
    targets = [
        ("2347", "聯強"), ("3017", "奇鋐"), ("2376", "技嘉"),
        ("6213", "聯茂"), ("8996", "高力"), ("2368", "金像電"),
        ("8046", "南電"), ("3037", "欣興"), ("6770", "力積電"),
        ("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"),
    ]
    print("=" * 44)
    print("布林縮口掃描")
    print("=" * 44)
    found = scan_list(targets)
    if not found:
        print("目前無縮口或噴出訊號")
    for s in found:
        print(format_squeeze(s))
        print()

    print("─" * 44)
    print("全部標的帶寬明細：")
    for code, name in targets:
        s = analyze_squeeze(code, name)
        if s:
            mark = "◀縮口" if s["in_squeeze"] else ""
            print(f"  {name}{code}: 帶寬{s['bw']:.1f}% 分位{s['bw_rank']:.0f} "
                  f"量比{s['vol_ratio']:.1f} {mark}")
