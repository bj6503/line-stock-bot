import json
import os
import datetime

STATE_FILE = "daily_state.json"


def tw_today() -> str:
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d")


def save_state(data: dict):
    data["date"] = tw_today()
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"已儲存 {STATE_FILE}")
    except Exception as e:
        print(f"儲存失敗: {e}")


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        print("無早盤記錄")
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != tw_today():
            print(f"記錄非今日（{data.get('date')}），略過")
            return {}
        return data
    except Exception as e:
        print(f"讀取失敗: {e}")
        return {}


def build_watch_entries(golden: list, picks: dict, risks: dict) -> list:
    """把早盤推薦整理成待追蹤清單"""
    entries, seen = [], set()

    def risk_info(code):
        r = risks.get(code)
        if not r:
            return {}
        res = r.get("ladder", {}).get("resistance", [])
        sup = r.get("ladder", {}).get("support", [])
        burst = r.get("burst", {})
        return {
            "resistance": res[0]["price"] if res else None,
            "support": sup[0]["price"] if sup else None,
            "support2": sup[1]["price"] if len(sup) >= 2 else None,
            "start_price": burst.get("start_price"),
        }

    for g in golden:
        code = g["code"]
        if code in seen:
            continue
        seen.add(code)
        e = {
            "code": code, "name": g["name"], "source": "黃金組合",
            "ref_price": g["bb"]["close"], "signal": g["bb"]["signal"],
        }
        e.update(risk_info(code))
        entries.append(e)

    for label, key in [("雙主力", "both"), ("投信", "trust"), ("外資", "foreign")]:
        for x in (picks or {}).get(key, []):
            code = x["code"]
            if code in seen:
                continue
            seen.add(code)
            e = {
                "code": code, "name": x["name"], "source": label,
                "ref_price": x["close"], "signal": f"{label}買超連{x.get('streak', 0)}日",
            }
            e.update(risk_info(code))
            entries.append(e)

    return entries
