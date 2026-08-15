import os
import requests
import datetime

TOKEN = os.environ.get("FINMIND_TOKEN", "")
API_URL = "https://api.finmindtrade.com/api/v4/data"

ETF_PREFIX = ("00",)


def fm_query(dataset: str, data_id: str = "", start_date: str = "", end_date: str = "") -> list:
    params = {"dataset": dataset, "token": TOKEN}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    try:
        r = requests.get(API_URL, params=params, timeout=30)
        j = r.json()
        if j.get("status") != 200:
            print(f"  API失敗 {dataset}: {j.get('msg')}")
            return []
        return j.get("data", [])
    except Exception as e:
        print(f"  API錯誤 {dataset}: {e}")
        return []


def is_etf(code: str) -> bool:
    return code.startswith(ETF_PREFIX)


def get_recent_trading_days(n: int = 6) -> list:
    """取得最近n個交易日（用台積電有資料的日子推）"""
    start = (datetime.date.today() - datetime.timedelta(days=n * 3)).strftime("%Y-%m-%d")
    data = fm_query("TaiwanStockPrice", "2330", start)
    dates = sorted({d["date"] for d in data})
    return dates[-n:] if len(dates) >= n else dates


def collect_flows(days: list) -> dict:
    """收集這幾天全市場三大法人買賣超"""
    flows = {}  # code -> {date -> {foreign, trust}}
    for d in days:
        data = fm_query("TaiwanStockInstitutionalInvestorsBuySell", "", d, d)
        print(f"  {d}: {len(data)} 筆")
        for row in data:
            code = row.get("stock_id", "")
            name = row.get("name", "")
            if not code:
                continue
            net = (float(row.get("buy", 0)) - float(row.get("sell", 0))) / 1000
            flows.setdefault(code, {}).setdefault(d, {"foreign": 0, "trust": 0})
            if name == "Foreign_Investor":
                flows[code][d]["foreign"] += net
            elif name == "Investment_Trust":
                flows[code][d]["trust"] += net
    return flows


def get_volumes(codes: list, date: str) -> dict:
    """取得指定日期各股成交量（張）"""
    data = fm_query("TaiwanStockPrice", "", date, date)
    vol = {}
    for row in data:
        code = row.get("stock_id", "")
        if code:
            vol[code] = float(row.get("Trading_Volume", 0)) / 1000
    return vol


def score_stocks(flows: dict, volumes: dict, days: list, investor: str) -> list:
    """計算每支股票的主力買超強度"""
    results = []
    last_day = days[-1]

    for code, by_date in flows.items():
        last = by_date.get(last_day, {})
        net_last = last.get(investor, 0)
        if net_last <= 0:
            continue

        # 連續買超天數
        streak = 0
        for d in reversed(days):
            if by_date.get(d, {}).get(investor, 0) > 0:
                streak += 1
            else:
                break

        # 期間累計
        total = sum(by_date.get(d, {}).get(investor, 0) for d in days)

        # 佔成交量比重
        vol = volumes.get(code, 0)
        ratio = (net_last / vol * 100) if vol > 0 else 0

        # 綜合分數：連續天數 x2 + 量比重 + 累計買超權重
        score = streak * 2 + min(ratio, 30) + min(total / 1000, 10)

        results.append({
            "code": code,
            "net_last": net_last,
            "total": total,
            "streak": streak,
            "ratio": ratio,
            "volume": vol,
            "score": score,
            "is_etf": is_etf(code),
        })

    results.sort(key=lambda x: -x["score"])
    return results


def get_daily_picks() -> dict:
    print("取得交易日...")
    days = get_recent_trading_days(6)
    if not days:
        return {}
    print(f"  區間: {days[0]} ~ {days[-1]}")

    print("收集三大法人資料...")
    flows = collect_flows(days)
    print(f"  共 {len(flows)} 支股票")

    print("取得成交量...")
    volumes = get_volumes(list(flows.keys()), days[-1])

    foreign_all = score_stocks(flows, volumes, days, "foreign")
    trust_all = score_stocks(flows, volumes, days, "trust")

    # 個股 vs ETF 分開
    foreign_stocks = [x for x in foreign_all if not x["is_etf"]][:5]
    trust_stocks = [x for x in trust_all if not x["is_etf"]][:5]
    etf_flows = [x for x in foreign_all if x["is_etf"]][:3]

    # 雙主力同買
    f_codes = {x["code"] for x in foreign_all[:40] if not x["is_etf"]}
    t_codes = {x["code"] for x in trust_all[:40] if not x["is_etf"]}
    both_codes = f_codes & t_codes
    both = []
    for code in both_codes:
        f = next((x for x in foreign_all if x["code"] == code), None)
        t = next((x for x in trust_all if x["code"] == code), None)
        if f and t:
            both.append({
                "code": code,
                "foreign": f["net_last"],
                "trust": t["net_last"],
                "streak": max(f["streak"], t["streak"]),
                "score": f["score"] + t["score"],
            })
    both.sort(key=lambda x: -x["score"])

    return {
        "date": days[-1],
        "foreign": foreign_stocks,
        "trust": trust_stocks,
        "etf": etf_flows,
        "both": both[:3],
    }


def format_picks(picks: dict, names: dict = None) -> str:
    if not picks:
        return "無法取得法人資料"
    names = names or {}

    def nm(code):
        return names.get(code, code)

    lines = [f"📋 {picks['date']} 主力動向", "═" * 16]

    if picks.get("both"):
        lines.append("🔥 雙主力同買")
        for b in picks["both"]:
            lines.append(f"  {nm(b['code'])} {b['code']}")
            lines.append(f"  外資{b['foreign']:+.0f} 投信{b['trust']:+.0f}張 連{b['streak']}日")
        lines.append("─" * 16)

    lines.append("🏦 外資買超前5")
    for i, x in enumerate(picks["foreign"], 1):
        lines.append(f"{i}. {nm(x['code'])} {x['code']}")
        lines.append(f"   +{x['net_last']:.0f}張 連{x['streak']}日 佔量{x['ratio']:.1f}%")
    lines.append("─" * 16)

    lines.append("🎯 投信買超前5")
    for i, x in enumerate(picks["trust"], 1):
        lines.append(f"{i}. {nm(x['code'])} {x['code']}")
        lines.append(f"   +{x['net_last']:.0f}張 連{x['streak']}日 佔量{x['ratio']:.1f}%")

    if picks.get("etf"):
        lines.append("─" * 16)
        lines.append("📊 ETF資金（大盤氣氛）")
        for x in picks["etf"]:
            lines.append(f"  {nm(x['code'])} {x['code']} +{x['net_last']:.0f}張")

    return "\n".join(lines)


if __name__ == "__main__":
    picks = get_daily_picks()
    print()
    print(format_picks(picks))
