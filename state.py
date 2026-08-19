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
        print(f"已儲存 {STATE_FILE}（{data['date']}）")
    except Exception as e:
        print(f"儲存失敗: {e}")


def load_state(allow_stale: bool = False) -> dict:
    """allow_stale=True 時，即使不是今天的記錄也回傳（供檢視用）"""
    if not os.path.exists(STATE_FILE):
        print("無早盤記錄")
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"讀取失敗: {e}")
        return {}

    if data.get("date") != tw_today() and not allow_stale:
        print(f"記錄非今日（{data.get('date')}），略過")
        return {}
    return data
