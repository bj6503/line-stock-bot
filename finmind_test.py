import os
import requests
import datetime

TOKEN = os.environ["FINMIND_TOKEN"]
API_URL = "https://api.finmindtrade.com/api/v4/data"

def query(dataset: str, data_id: str = "", start_date: str = "", extra: dict = None) -> list:
    params = {
        "dataset": dataset,
        "token": TOKEN,
    }
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    if extra:
        params.update(extra)
    try:
        r = requests.get(API_URL, params=params, timeout=15)
        j = r.json()
        if j.get("status") != 200:
            print(f"  ❌ {dataset} 失敗: {j.get('msg', r.status_code)}")
            return []
        data = j.get("data", [])
        print(f"  ✅ {dataset} 取得 {len(data)} 筆")
        return data
    except Exception as e:
        print(f"  ❌ {dataset} 錯誤: {e}")
        return []

def main():
    # 用台積電當測試標的，抓最近10天
    stock = "2330"
    start = (datetime.date.today() - datetime.timedelta(days=14)).strftime("%Y-%m-%d")

    print("=" * 50)
    print(f"FinMind 資料品質測試（標的:{stock} 起始:{start}）")
    print("=" * 50)

    print("\n【1. 融資融券】← 斷頭推估的核心")
    data = query("TaiwanStockMarginPurchaseShortSale", stock, start)
    if data:
        last = data[-1]
        print(f"  最新一筆: {last.get('date')}")
        print(f"  融資餘額: {last.get('MarginPurchaseTodayBalance')}")
        print(f"  融券餘額: {last.get('ShortSaleTodayBalance')}")

    print("\n【2. 三大法人買賣超】")
    data = query("TaiwanStockInstitutionalInvestorsBuySell", stock, start)
    if data:
        last = data[-1]
        print(f"  最新一筆: {last.get('date')} {last.get('name')}")
        print(f"  買: {last.get('buy')} 賣: {last.get('sell')}")

    print("\n【3. 借券賣出（外資空單）】")
    data = query("TaiwanStockSecuritiesLending", stock, start)
    if data:
        print(f"  最新一筆: {data[-1].get('date')}")

    print("\n【4. 日K線】")
    data = query("TaiwanStockPrice", stock, start)
    if data:
        last = data[-1]
        print(f"  最新: {last.get('date')} 收盤 {last.get('close')} 量 {last.get('Trading_Volume')}")

    print("\n【5. 5分K（測試免費版是否可用）】")
    data = query("TaiwanStockPriceTick", stock, datetime.date.today().strftime("%Y-%m-%d"))
    if not data:
        print("  （免費版可能不含此資料集，之後評估是否付費）")

    print("\n【6. 匯率 USD/TWD】")
    data = query("TaiwanExchangeRate", "USD", start)
    if data:
        last = data[-1]
        print(f"  最新: {last.get('date')} 現金賣出 {last.get('cash_sell')} 即期賣出 {last.get('spot_sell')}")

    print("\n【7. 台指期夜盤】")
    data = query("TaiwanFuturesDaily", "TX", start)
    if data:
        print(f"  最新: {data[-1].get('date')} 收盤 {data[-1].get('close')}")

    print("\n" + "=" * 50)
    print("測試完成！把上面整段結果貼給 Claude 分析")
    print("=" * 50)

if __name__ == "__main__":
    main()
