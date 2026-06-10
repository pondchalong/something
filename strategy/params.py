"""
StrategyParams — รวม parameter ทั้งหมดของ strategy ไว้ที่เดียว
เพื่อให้ optimizer ปรับได้ (เดิม hardcode กระจัดกระจาย)

DEFAULT_PARAMS = ค่าเดิมของ Phase 1 → ใส่ default ทุกที่ ให้ Phase 1 ทำงานเหมือนเดิม
active_params.json = strategy ที่ approve แล้ว (executor ใช้ตัวนี้)
"""
import json
import os
from dataclasses import dataclass, asdict, fields

ACTIVE_PATH = os.path.join(os.path.dirname(__file__), "active_params.json")


@dataclass
class StrategyParams:
    # Risk / exit
    atr_multiplier: float = 1.5      # SL = ATR × นี้
    risk_reward: float = 2.0         # TP = SL × นี้

    # Signal filter
    confluence_min: int = 3          # confluence ต่ำกว่านี้ → ไม่ส่งสัญญาณ
    htf_filter: bool = False         # True = เทรดเฉพาะตาม HTF MACD (trend ใหญ่)
    skip_high_vol: bool = False      # True = ไม่เทรดตอน volatility = HIGH

    # SuperTrend (ML Adaptive)
    st_factor: float = 3.0
    st_atr_len: int = 10

    # Indicators
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_len: int = 14

    # Data
    timeframe: str = "15m"

    # Position management
    reverse: bool = False    # True = ถือไม้ + signal ตรงข้าม → ปิด + กลับข้างทันที
    max_pyramid: int = 1     # จำนวนไม้สูงสุดต่อ position (1 = ไม่ pyramid); >1 = เพิ่มไม้เมื่อ signal เดิมทาง

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyParams":
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})


DEFAULT_PARAMS = StrategyParams()


def load_active() -> StrategyParams:
    """อ่าน strategy ที่ approve แล้ว — ถ้ายังไม่มี ใช้ default"""
    if os.path.exists(ACTIVE_PATH):
        with open(ACTIVE_PATH, "r", encoding="utf-8") as f:
            return StrategyParams.from_dict(json.load(f))
    return DEFAULT_PARAMS


def save_active(params: StrategyParams) -> None:
    """บันทึก strategy ที่ approve (manual approve จาก dashboard)"""
    with open(ACTIVE_PATH, "w", encoding="utf-8") as f:
        json.dump(params.to_dict(), f, indent=2)
