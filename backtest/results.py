"""
เก็บ/อ่านผล backtest + optimizer

- results/<name>.json : ผล backtest แต่ละครั้ง (dashboard อ่าน)
- candidate.json      : best params จาก optimizer (รอ manual approve)
"""
import json
import os
from datetime import datetime

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
CANDIDATE_PATH = os.path.join(RESULTS_DIR, "candidate.json")

os.makedirs(RESULTS_DIR, exist_ok=True)


def save_result(name: str, result, extra: dict = None) -> str:
    """บันทึก BacktestResult เป็น JSON"""
    path = os.path.join(RESULTS_DIR, f"{name}.json")
    payload = {
        "name": name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "params": result.params,
        "metrics": result.metrics,
        "equity_curve": result.equity_curve,
        "trades": result.trades,
    }
    if extra:
        payload.update(extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def list_results() -> list:
    """ชื่อ result ทั้งหมด (ไม่รวม candidate)"""
    if not os.path.isdir(RESULTS_DIR):
        return []
    return sorted(
        f[:-5] for f in os.listdir(RESULTS_DIR)
        if f.endswith(".json") and f != "candidate.json"
    )


def load_result(name: str) -> dict:
    path = os.path.join(RESULTS_DIR, f"{name}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_candidate(params: dict, train_metrics: dict, test_metrics: dict) -> None:
    """best params จาก optimizer → รอ approve ใน dashboard"""
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "params": params,
        "train_metrics": train_metrics,
        "test_metrics": test_metrics,
    }
    with open(CANDIDATE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_candidate() -> dict | None:
    if os.path.exists(CANDIDATE_PATH):
        with open(CANDIDATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return None
