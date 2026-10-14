"""
เก็บข้อมูลเทรดจริงจาก Binance testnet ลง records/ (เข้า git) + สรุปแยกตามยุคของโค้ด

ทำไมต้องเก็บ: ข้อมูลบน exchange มีอายุ — order ปกติ query ได้แค่ 90 วัน (-4166),
fill ได้ 6 เดือน. ถ้าไม่ export ไว้ หายถาวร. และ trade_log.json บน Railway
หายตามไปตอนเลิก deploy → records/ คือแหล่งข้อมูลเดียวที่เหลือ

รันซ้ำได้เรื่อยๆ: ของใหม่ถูก **merge** กับของเดิม (dedupe ด้วย id) ไม่ทับ —
ของที่หมดอายุบน exchange ไปแล้วยังอยู่ในไฟล์

ใช้ (ต้องมี BINANCE_TESTNET_API_KEY/SECRET ยกเว้น --offline):
    py -3.12 -m analysis.export_records              # ดึง + merge + สรุป
    py -3.12 -m analysis.export_records --offline    # สรุปจากไฟล์ใน records/ อย่างเดียว
"""
import argparse
import csv
import json
import os
import statistics as st
import time
from datetime import datetime

from analysis.reconcile import FETCH_WINDOW_MS, _f, fetch_fills, group_into_trades

RECORDS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "records")
FILLS_FILE = "binance_fills.json"
ALGO_FILE = "binance_algo_orders.json"      # SL/TP (conditional)
ORDERS_FILE = "binance_orders.json"         # order ปกติ (market entry / บอทปิดเอง)
TRADES_FILE = "trades.csv"
META_FILE = "meta.json"

DEFAULT_SINCE = "2026-05-25"
ORDER_HISTORY_DAYS = 89                     # Binance ให้ย้อนได้ 90 วัน (กันขอบ)
RISK_REWARD = 2.0                           # ใช้คำนวณ R ตอนรู้แค่ TP (ทุกยุคใช้ 2.0)
STOP_MATCH_MS = 10 * 60 * 1000              # SL/TP ต้องถูกตั้งภายในกี่ ms หลังเข้าไม้

# ยุคของโค้ดที่ live — ไม้ต่างยุคห้ามรวมวัดผล (เวลาท้องถิ่น ≈ เวลา deploy)
ERAS = [
    ("2026-06-01 00:00", "E0_buggy",
     "execution มีบั๊ก exit price / phantom close / SL-TP ค้าง — ห้ามใช้วัด strategy"),
    ("2026-07-28 04:30", "E1_exit_fixed",
     "แก้บั๊ก exit + supervisor แล้ว ยังไม่มีพื้น SL (ตลาดนิ่ง = ไม้ขยะ)"),
    ("2026-08-16 04:31", "E2_sl_floor",
     "พื้น SL 0.15% + เพดาน notional 8x"),
]


def _ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").timestamp() * 1000)


def era_of(ts_ms: int) -> str:
    name = ERAS[0][1]
    for start, n, _ in ERAS:
        if ts_ms >= _ms(start):
            name = n
    return name


# ============================================================
# normalize + merge
# ============================================================
def normalize_algo(raw: dict) -> dict:
    """conditional order (SL/TP) — ccxt ไม่ normalize ฟิลด์พวกนี้ ต้องอ่านจาก info"""
    i = raw.get("info") or {}
    return {
        "algo_id": str(i.get("algoId") or raw.get("id") or ""),
        "type": i.get("orderType"),                        # STOP_MARKET / TAKE_PROFIT_MARKET
        "side": (i.get("side") or "").lower(),
        "qty": _f(i.get("quantity")),
        "trigger": _f(i.get("triggerPrice")),
        "status": i.get("algoStatus"),                     # FINISHED / CANCELED / REJECTED
        "actual_price": _f(i.get("actualPrice")) or None,  # ราคาที่ fill จริงหลัง trigger
        "actual_order_id": str(i.get("actualOrderId") or ""),
        "create_ms": int(_f(i.get("createTime"))),
        "trigger_ms": int(_f(i.get("triggerTime"))) or None,
        "update_ms": int(_f(i.get("updateTime"))) or None,
        "reject_reason": i.get("rejectReason") or None,
        "working_type": i.get("workingType"),
    }


def normalize_order(raw: dict) -> dict:
    i = raw.get("info") or {}
    return {
        "order_id": str(i.get("orderId") or raw.get("id") or ""),
        "type": i.get("origType") or i.get("type"),
        "side": (i.get("side") or "").lower(),
        "qty": _f(i.get("origQty")),
        "executed_qty": _f(i.get("executedQty")),
        "avg_price": _f(i.get("avgPrice")) or None,
        "status": i.get("status"),
        "reduce_only": bool(i.get("reduceOnly")),
        "time_ms": int(_f(i.get("time"))),
    }


def _ts(r: dict) -> int:
    return r.get("ts") or r.get("create_ms") or r.get("time_ms") or 0


def merge(old: list, new: list, key: str) -> list:
    """รวมของเดิมกับของใหม่ (ของใหม่ทับเมื่อ id ซ้ำ — สถานะอาจอัปเดต) เรียงตามเวลา"""
    by = {r[key]: r for r in old if r.get(key)}
    for r in new:
        if r.get(key):
            by[r[key]] = r
    return sorted(by.values(), key=_ts)


def _fetch_windows(ex, symbol, since_ms, until_ms, params):
    out, cur = [], since_ms
    while cur <= until_ms:
        end = min(cur + FETCH_WINDOW_MS, until_ms)
        out += ex.fetch_orders(symbol, since=cur, limit=1000, params={**params, "endTime": end})
        cur = end + 1
    return out


# ============================================================
# ประกอบไม้ + จับคู่กับ SL/TP
# ============================================================
def match_stops(trade: dict, algos: list, history_start_ms: int = None) -> dict:
    """
    หา SL/TP ของไม้นี้ + ไม้จบด้วยอะไร

    exit_via:
      SL / TP      — order ที่ตั้งตอนเปิดไม้นี้ trigger แล้วปิด position
      stale_order  — โดน SL/TP ของ "ไม้ก่อนหน้า" ปิด (บั๊กยุค E0: ไม่ cancel order ค้าง)
      bot_market   — มี SL/TP แต่บอทปิดเองด้วย market (orphan / reverse / ปิดกันเปลือย)
      no_stops     — ไม่มี SL/TP ถูกตั้งเลย (ตั้งไม่ผ่าน -2021 แล้วบอทปิดทิ้ง)
      no_history   — ไม้เก่ากว่าประวัติ SL/TP ที่ exchange ยังเก็บไว้ = ไม่รู้ (ไม่ใช่บั๊ก)
    """
    exit_side = "sell" if trade["side"] == "LONG" else "buy"
    mine = [a for a in algos
            if a["side"] == exit_side
            and 0 <= a["create_ms"] - trade["entry_ts"] <= STOP_MATCH_MS
            and a["create_ms"] <= trade["exit_ts"]]
    sl = next((a for a in mine if a["type"] == "STOP_MARKET"), None)
    tp = next((a for a in mine if a["type"] == "TAKE_PROFIT_MARKET"), None)

    closes = set(trade.get("close_order_ids") or [])
    hit = next((a for a in algos if a["actual_order_id"] and a["actual_order_id"] in closes), None)
    if hit is not None and hit in mine:
        via = "SL" if hit["type"] == "STOP_MARKET" else "TP"
    elif hit is not None:
        via = "stale_order"
    elif mine:
        via = "bot_market"
    elif history_start_ms and trade["entry_ts"] < history_start_ms:
        via = "no_history"
    else:
        via = "no_stops"

    risk = None
    if sl:
        risk = abs(trade["entry"] - sl["trigger"])
    elif tp:
        risk = abs(tp["trigger"] - trade["entry"]) / RISK_REWARD

    slip = None
    if hit is not None and hit.get("actual_price") and hit["trigger"]:
        adverse = (hit["trigger"] - hit["actual_price"]) if exit_side == "sell" \
            else (hit["actual_price"] - hit["trigger"])
        slip = adverse / hit["trigger"] * 10000
    return {"sl": sl["trigger"] if sl else None, "tp": tp["trigger"] if tp else None,
            "exit_via": via, "risk": risk or None, "exit_slip_bps": slip}


def build_trades(fills: list, algos: list) -> list:
    rows = []
    history_start = min((a["create_ms"] for a in algos), default=None)
    for t in group_into_trades(fills):
        if t.get("open_position"):
            continue
        m = match_stops(t, algos, history_start)
        move = (t["exit"] - t["entry"]) if t["side"] == "LONG" else (t["entry"] - t["exit"])
        notional = t["entry"] * t["qty"]
        risk = m["risk"]
        rows.append({
            "era": era_of(t["entry_ts"]),
            "side": t["side"],
            "entry_time": datetime.fromtimestamp(t["entry_ts"] / 1000).isoformat(timespec="seconds"),
            "exit_time": datetime.fromtimestamp(t["exit_ts"] / 1000).isoformat(timespec="seconds"),
            "hold_min": round((t["exit_ts"] - t["entry_ts"]) / 60000, 1),
            "qty": round(t["qty"], 6),
            "entry": round(t["entry"], 2),
            "exit": round(t["exit"], 2),
            "notional": round(notional, 2),
            "gross_usdt": round(t["realized_pnl"], 4),
            "fee_usdt": round(t["commission"], 4),
            "net_usdt": round(t["net_pnl"], 4),
            "gross_pct": round(t["realized_pnl"] / notional * 100, 4) if notional else None,
            "net_pct": round(t["net_pnl"] / notional * 100, 4) if notional else None,
            "sl": m["sl"], "tp": m["tp"],
            "sl_pct": round(risk / t["entry"] * 100, 4) if risk else None,
            "r_multiple": round(move / risk, 3) if risk else None,
            "fee_r": round(t["commission"] / (risk * t["qty"]), 3) if risk else None,
            "exit_via": m["exit_via"],
            "exit_slip_bps": round(m["exit_slip_bps"], 2) if m["exit_slip_bps"] is not None else None,
            "n_fills": t["n_fills"],
        })
    return rows


# ============================================================
# สรุป
# ============================================================
def summarize(rows: list) -> dict:
    if not rows:
        return {"n": 0}
    n = len(rows)
    notional = sum(r["notional"] for r in rows)
    gross = sum(r["gross_usdt"] for r in rows)
    fee = sum(r["fee_usdt"] for r in rows)
    wins = [r for r in rows if r["net_usdt"] > 0]
    loss = [r for r in rows if r["net_usdt"] <= 0]
    via = {}
    for r in rows:
        via[r["exit_via"]] = via.get(r["exit_via"], 0) + 1

    def col(k, field):
        return [r[field] for r in rows if r["exit_via"] == k and r[field] is not None]

    fr = [r["fee_r"] for r in rows if r["fee_r"] is not None]
    days = (datetime.fromisoformat(rows[-1]["exit_time"])
            - datetime.fromisoformat(rows[0]["entry_time"])).total_seconds() / 86400
    out = {
        "n": n, "days": round(days, 1), "per_day": round(n / days, 2) if days else None,
        "net_usdt": round(gross - fee, 2), "gross_usdt": round(gross, 2), "fee_usdt": round(fee, 2),
        "winrate": round(len(wins) / n * 100, 1),
        "pf": round(sum(r["net_usdt"] for r in wins) / abs(sum(r["net_usdt"] for r in loss)), 2)
        if loss else None,
        "gross_per_trade_pct": round(gross / notional * 100, 4),
        "net_per_trade_pct": round((gross - fee) / notional * 100, 4),
        "exit_via": via,
        "junk_under_2min": sum(1 for r in rows if r["hold_min"] < 2),
        "fee_r_median": round(st.median(fr), 3) if fr else None,
    }
    for k in ("SL", "TP"):
        if col(k, "r_multiple"):
            out[f"{k}_r_mean"] = round(st.mean(col(k, "r_multiple")), 3)
            out[f"{k}_r_median"] = round(st.median(col(k, "r_multiple")), 3)
        if col(k, "exit_slip_bps"):
            out[f"{k}_slip_bps_median"] = round(st.median(col(k, "exit_slip_bps")), 2)
            out[f"{k}_slip_bps_mean"] = round(st.mean(col(k, "exit_slip_bps")), 2)
    # winrate ที่ต้องได้ถึงจะคุ้ม: w×W − (1−w)×L − fee = 0
    if out.get("TP_r_mean") and out.get("SL_r_mean") and out["fee_r_median"] is not None:
        W, L = out["TP_r_mean"], abs(out["SL_r_mean"])
        out["breakeven_winrate"] = round((L + out["fee_r_median"]) / (W + L) * 100, 1)
    return out


def print_summary(rows: list) -> None:
    print(f"\n{'ยุค':<15}{'ไม้':>5}{'net USDT':>11}{'winrate':>9}{'PF':>6}"
          f"{'gross/ไม้':>11}{'net/ไม้':>10}{'fee/R':>7}{'ขยะ<2น':>8}  exit")
    for _, name, _ in ERAS:
        s = summarize([r for r in rows if r["era"] == name])
        if not s["n"]:
            continue
        print(f"{name:<15}{s['n']:>5}{s['net_usdt']:>+11.2f}{s['winrate']:>8.1f}%{s['pf'] or 0:>6.2f}"
              f"{s['gross_per_trade_pct']:>+10.3f}%{s['net_per_trade_pct']:>+9.3f}%"
              f"{s['fee_r_median'] or 0:>7.2f}{s['junk_under_2min']:>8}  {s['exit_via']}")


# ============================================================
def _load(name):
    p = os.path.join(RECORDS_DIR, name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save(name, data):
    with open(os.path.join(RECORDS_DIR, name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser(description="เก็บข้อมูลเทรดจริงลง records/ + สรุป")
    ap.add_argument("--since", default=DEFAULT_SINCE, help="ดึงตั้งแต่วันไหน (YYYY-MM-DD)")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--offline", action="store_true", help="ไม่ต่อ network สรุปจากไฟล์อย่างเดียว")
    args = ap.parse_args()
    os.makedirs(RECORDS_DIR, exist_ok=True)

    fills = _load(FILLS_FILE) or []
    algos = _load(ALGO_FILE) or []
    orders = _load(ORDERS_FILE) or []
    meta = {}
    if not args.offline:
        from config import SYMBOL
        from data.fetcher import get_testnet_exchange
        symbol = args.symbol or SYMBOL
        ex = get_testnet_exchange()
        ex.load_markets()
        now = int(time.time() * 1000)
        since = int(datetime.strptime(args.since, "%Y-%m-%d").timestamp() * 1000)
        print(f"ดึง fill ตั้งแต่ {args.since} ...")
        new_fills = fetch_fills(ex, symbol, since, now)
        print("ดึง SL/TP (conditional) ...")
        new_algos = [normalize_algo(o) for o in _fetch_windows(ex, symbol, since, now, {"stop": True})]
        print(f"ดึง order ปกติ (Binance ให้ย้อนได้แค่ {ORDER_HISTORY_DAYS + 1} วัน) ...")
        o_since = max(since, now - ORDER_HISTORY_DAYS * 86400 * 1000)
        new_orders = [normalize_order(o) for o in _fetch_windows(ex, symbol, o_since, now, {})]
        before = (len(fills), len(algos), len(orders))
        fills = merge(fills, new_fills, "trade_id")
        algos = merge(algos, new_algos, "algo_id")
        orders = merge(orders, new_orders, "order_id")
        print(f"fill {before[0]} → {len(fills)} | SL/TP {before[1]} → {len(algos)} | "
              f"order {before[2]} → {len(orders)}")
        _save(FILLS_FILE, fills)
        _save(ALGO_FILE, algos)
        _save(ORDERS_FILE, orders)
        try:
            meta["balance_usdt_at_export"] = round(float(ex.fetch_balance()["USDT"]["total"]), 2)
        except Exception as e:
            print(f"(ดึง balance ไม่ได้: {str(e)[:60]})")
        meta.update({"exported_at": datetime.now().isoformat(timespec="seconds"),
                     "symbol": symbol, "source": "Binance USD-M futures testnet"})

    rows = build_trades(fills, algos)
    if rows:
        with open(os.path.join(RECORDS_DIR, TRADES_FILE), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    if not args.offline:
        meta.update({"fills": len(fills), "algo_orders": len(algos), "orders": len(orders)})
    if fills:
        meta.update({
            "trades": len(rows),
            "first_fill": datetime.fromtimestamp(fills[0]["ts"] / 1000).isoformat(timespec="seconds")
            if fills else None,
            "last_fill": datetime.fromtimestamp(fills[-1]["ts"] / 1000).isoformat(timespec="seconds")
            if fills else None,
            "eras": [{"start": s, "name": n, "note": d} for s, n, d in ERAS],
            "summary_by_era": {n: summarize([r for r in rows if r["era"] == n]) for _, n, _ in ERAS},
        })
        _save(META_FILE, {**(_load(META_FILE) or {}), **meta})   # --offline ก็อัปเดตสรุปด้วย
    print(f"\nไม้ปิด {len(rows)} ไม้ → records/{TRADES_FILE}")
    print_summary(rows)


if __name__ == "__main__":
    main()
