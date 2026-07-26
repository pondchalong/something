"""
กระทบยอด trade_log.json (ที่ bot บันทึกเอง) กับ fill จริงจาก Binance API

ทำไมต้องมี: exit price ที่ bot บันทึกเคยเพี้ยน ~50% ของไม้ (ดู CLAUDE.md
"exit price ที่บันทึกเชื่อไม่ได้") → PnL ที่เอาไปตัดสิน strategy เชื่อไม่ได้
fill ของ exchange มี **realizedPnl ติดมาด้วย** = ground truth ที่บั๊กฝั่งเราแตะไม่ได้

ใช้ยังไง (ต้องมี BINANCE_TESTNET_API_KEY/SECRET):
    py -3.12 -m analysis.reconcile                  # ดึงสด + เทียบกับ trade_log.json
    py -3.12 -m analysis.reconcile --days 60
    py -3.12 -m analysis.reconcile --dump fills.json    # เก็บ raw ไว้ (เอาไปวิเคราะห์ที่อื่นได้)
    py -3.12 -m analysis.reconcile --file fills.json    # อ่านจากไฟล์ ไม่ต้องต่อ network
"""
import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime

# ระยะเวลาที่ยอมให้ entry ของ log กับ fill จริงห่างกันแล้วยังถือว่าเป็นไม้เดียวกัน
MATCH_WINDOW_MS = 10 * 60 * 1000
PAGE = 1000


# ============================================================
# ดึงข้อมูลจาก exchange
# ============================================================
def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalize_fill(raw: dict) -> dict:
    """
    ccxt คืน fill แบบ normalize แต่ realizedPnl/commission อยู่ใน info (ของ Binance ตรงๆ)
    """
    info = raw.get("info") or {}
    return {
        "ts": int(raw.get("timestamp") or _f(info.get("time"))),
        "side": (raw.get("side") or info.get("side", "")).lower(),
        "price": _f(raw.get("price") or info.get("price")),
        "qty": _f(raw.get("amount") or info.get("qty")),
        "realized_pnl": _f(info.get("realizedPnl")),
        "commission": _f(info.get("commission")),
        "order_id": str(raw.get("order") or info.get("orderId") or ""),
    }


def fetch_fills(ex, symbol: str, since_ms: int) -> list:
    """ดึง fill ทั้งหมดตั้งแต่ since_ms (paginate — Binance คืนสูงสุด 1000/ครั้ง)"""
    out, cursor, seen = [], since_ms, set()
    while True:
        batch = ex.fetch_my_trades(symbol, since=cursor, limit=PAGE)
        if not batch:
            break
        fresh = 0
        for raw in batch:
            f = normalize_fill(raw)
            key = (f["order_id"], f["ts"], f["price"], f["qty"])
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
            fresh += 1
        if fresh == 0 or len(batch) < PAGE:
            break
        cursor = max(f["ts"] for f in out) + 1
    out.sort(key=lambda f: f["ts"])
    return out


# ============================================================
# ประกอบ fill → ไม้ (round trip)
# ============================================================
def group_into_trades(fills: list) -> list:
    """
    ประกอบ fill เป็นไม้: ไล่ position สะสม พอกลับมา 0 = ไม้นั้นปิด

    ทำแบบนี้ได้ผลจริงเพราะไม่ต้องพึ่ง state ฝั่งเรา (ซึ่งเป็นตัวที่พัง) —
    ใช้แค่ลำดับ fill ของ exchange เอง
    """
    trades, pos, cur = [], 0.0, None
    for f in fills:
        signed = f["qty"] if f["side"] == "buy" else -f["qty"]
        if cur is None:
            cur = {"side": "LONG" if signed > 0 else "SHORT",
                   "open_qty": 0.0, "open_notional": 0.0,
                   "close_qty": 0.0, "close_notional": 0.0,
                   "realized_pnl": 0.0, "commission": 0.0,
                   "entry_ts": f["ts"], "exit_ts": f["ts"], "n_fills": 0}
        cur["n_fills"] += 1
        cur["commission"] += f["commission"]
        cur["realized_pnl"] += f["realized_pnl"]
        cur["exit_ts"] = f["ts"]

        opening = (pos >= 0 and signed > 0) or (pos <= 0 and signed < 0)
        if opening:
            cur["open_qty"] += abs(signed)
            cur["open_notional"] += abs(signed) * f["price"]
        else:
            cur["close_qty"] += abs(signed)
            cur["close_notional"] += abs(signed) * f["price"]

        pos = round(pos + signed, 10)
        if pos == 0:
            cur["entry"] = (cur["open_notional"] / cur["open_qty"]) if cur["open_qty"] else 0.0
            cur["exit"] = (cur["close_notional"] / cur["close_qty"]) if cur["close_qty"] else 0.0
            cur["qty"] = cur["open_qty"]
            # PnL สุทธิเป็น % ของ notional ที่เข้า (เทียบกับ pnl_pct ที่ bot บันทึกได้ตรงๆ)
            notional = cur["entry"] * cur["qty"]
            net = cur["realized_pnl"] - cur["commission"]
            cur["net_pnl"] = net
            cur["pnl_pct"] = (net / notional) if notional else 0.0
            trades.append(cur)
            cur = None
    if cur is not None:                       # ไม้ที่ยังถืออยู่ (ยังไม่ปิด)
        cur["open_position"] = True
        cur["entry"] = (cur["open_notional"] / cur["open_qty"]) if cur["open_qty"] else 0.0
        trades.append(cur)
    return trades


# ============================================================
# เทียบกับ log ที่ bot บันทึกเอง
# ============================================================
def _log_entry_ms(t: dict):
    if t.get("entry_ms"):
        return int(t["entry_ms"])
    try:
        return int(datetime.fromisoformat(str(t["entry_time"])).timestamp() * 1000)
    except (ValueError, TypeError, KeyError):
        return None


def reconcile(api_trades: list, log_trades: list, price_tol_pct: float = 0.001) -> dict:
    """
    จับคู่ไม้ของ API กับ log ด้วยเวลาเปิดไม้ แล้วดูว่า exit/PnL ตรงกันไหม

    price_tol_pct: ยอมให้ราคาต่างกันกี่ % ถึงยังถือว่า "ตรงกัน" (default 0.1%)
    """
    closed_api = [t for t in api_trades if not t.get("open_position")]
    pairs, unmatched_log = [], []
    used = set()

    for lt in log_trades:
        ms = _log_entry_ms(lt)
        if ms is None:
            unmatched_log.append(lt)
            continue
        best, best_d = None, None
        for i, at in enumerate(closed_api):
            if i in used:
                continue
            d = abs(at["entry_ts"] - ms)
            if d <= MATCH_WINDOW_MS and (best_d is None or d < best_d):
                best, best_d = i, d
        if best is None:
            unmatched_log.append(lt)
        else:
            used.add(best)
            pairs.append((lt, closed_api[best]))

    exit_mismatch, pnl_mismatch = [], []
    for lt, at in pairs:
        le, ae = lt.get("exit"), at["exit"]
        if le is not None and ae and abs(_f(le) - ae) / ae > price_tol_pct:
            exit_mismatch.append({
                "entry_time": lt.get("entry_time"), "action": lt.get("action"),
                "log_exit": _f(le), "real_exit": round(ae, 2),
                "diff_pct": round((_f(le) - ae) / ae * 100, 3),
            })
        lp = lt.get("pnl_pct")
        if lp is not None and abs(_f(lp) - at["pnl_pct"]) > 0.0005:
            pnl_mismatch.append({
                "entry_time": lt.get("entry_time"),
                "log_pnl_pct": round(_f(lp) * 100, 3),
                "real_pnl_pct": round(at["pnl_pct"] * 100, 3),
            })

    log_sum = sum(_f(t.get("pnl_pct")) for t in log_trades if t.get("pnl_pct") is not None)
    return {
        "api_closed_trades": len(closed_api),
        "log_closed_trades": len(log_trades),
        "matched": len(pairs),
        "unmatched_log": len(unmatched_log),
        "unmatched_api": len(closed_api) - len(used),
        "exit_mismatch": len(exit_mismatch),
        "pnl_mismatch": len(pnl_mismatch),
        "real_net_pnl_usdt": round(sum(t["net_pnl"] for t in closed_api), 4),
        "real_commission_usdt": round(sum(t["commission"] for t in closed_api), 4),
        "real_sum_pnl_pct": round(sum(t["pnl_pct"] for t in closed_api) * 100, 2),
        "log_sum_pnl_pct": round(log_sum * 100, 2),
        "worst_exit_mismatch": sorted(exit_mismatch,
                                      key=lambda m: -abs(m["diff_pct"]))[:10],
        "worst_pnl_mismatch": sorted(
            pnl_mismatch, key=lambda m: -abs(m["log_pnl_pct"] - m["real_pnl_pct"]))[:10],
    }


def format_report(api_trades: list, rec: dict, balance=None) -> str:
    closed = [t for t in api_trades if not t.get("open_position")]
    wins = [t for t in closed if t["net_pnl"] > 0]
    L = ["=" * 64,
         "  กระทบยอด: trade_log ของ bot  vs  fill จริงจาก Binance",
         "=" * 64]
    if balance is not None:
        L.append(f"ยอดคงเหลือปัจจุบัน: {balance:,.2f} USDT")
    L.append("")
    L.append("── ของจริงจาก exchange ────────────────────────────────────")
    L.append(f"  ไม้ที่ปิดแล้ว     {len(closed)}")
    if closed:
        L.append(f"  Winrate          {len(wins)/len(closed)*100:.1f}%")
        L.append(f"  PnL สุทธิ         {rec['real_net_pnl_usdt']:+.2f} USDT "
                 f"(ค่าธรรมเนียม {rec['real_commission_usdt']:.2f})")
        L.append(f"  รวม PnL%         {rec['real_sum_pnl_pct']:+.2f}%")
    if any(t.get("open_position") for t in api_trades):
        L.append("  ⚠️  ยังมี position ค้างอยู่ 1 ไม้ (ไม่นับในสถิติ)")

    L.append("")
    L.append("── เทียบกับที่ bot บันทึกเอง ───────────────────────────────")
    L.append(f"  จับคู่ได้         {rec['matched']}/{rec['log_closed_trades']} ไม้ใน log")
    L.append(f"  log มีแต่ API ไม่มี  {rec['unmatched_log']}  "
             f"(ไม้ผีที่ bot บันทึกทั้งที่ไม่ได้ปิดจริง)")
    L.append(f"  API มีแต่ log ไม่มี  {rec['unmatched_api']}  (ไม้ที่ bot พลาดไม่ได้บันทึก)")
    L.append(f"  exit price ไม่ตรง   {rec['exit_mismatch']}")
    L.append(f"  PnL ไม่ตรง          {rec['pnl_mismatch']}")
    L.append("")
    L.append(f"  รวม PnL% ที่ bot บันทึก {rec['log_sum_pnl_pct']:+.2f}%  "
             f"vs ของจริง {rec['real_sum_pnl_pct']:+.2f}%")
    diff = rec["log_sum_pnl_pct"] - rec["real_sum_pnl_pct"]
    L.append(f"  → ต่างกัน {diff:+.2f} จุด "
             f"({'bot รายงานแย่กว่าความจริง' if diff < 0 else 'bot รายงานดีกว่าความจริง'})")

    if rec["worst_exit_mismatch"]:
        L.append("")
        L.append("── exit price ที่ผิดมากสุด ─────────────────────────────────")
        L.append(f"  {'เวลาเปิดไม้':<20}{'ทิศ':<7}{'log':>11}{'จริง':>11}{'ต่าง%':>9}")
        for m in rec["worst_exit_mismatch"]:
            L.append(f"  {str(m['entry_time'])[:19]:<20}{str(m['action']):<7}"
                     f"{m['log_exit']:>11.2f}{m['real_exit']:>11.2f}{m['diff_pct']:>+9.2f}")
    L.append("=" * 64)
    return "\n".join(L)


# ============================================================
def main():
    ap = argparse.ArgumentParser(description="กระทบยอด trade_log กับ fill จริงจาก Binance")
    ap.add_argument("--days", type=int, default=60, help="ดึงย้อนหลังกี่วัน (default 60)")
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--dump", help="เขียน fill ดิบเป็น JSON (เอาไปวิเคราะห์ offline ได้)")
    ap.add_argument("--file", help="อ่าน fill จากไฟล์ที่ dump ไว้ แทนการต่อ network")
    ap.add_argument("--log", help="path ของ trade_log.json (default = ตาม DATA_DIR)")
    args = ap.parse_args()

    from config import SYMBOL
    symbol = args.symbol or SYMBOL
    balance = None

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            fills = json.load(f)
        print(f"อ่าน {len(fills)} fill จาก {args.file}")
    else:
        from data.fetcher import get_testnet_exchange
        ex = get_testnet_exchange()
        ex.load_markets()
        since = int(time.time() * 1000) - args.days * 86400 * 1000
        print(f"ดึง fill ของ {symbol} ย้อนหลัง {args.days} วัน ...")
        fills = fetch_fills(ex, symbol, since)
        print(f"ได้ {len(fills)} fill")
        try:
            balance = float(ex.fetch_balance()["USDT"]["total"])
        except Exception as e:
            print(f"(ดึง balance ไม่ได้: {str(e)[:60]})")
        if args.dump:
            with open(args.dump, "w", encoding="utf-8") as f:
                json.dump(fills, f, indent=2)
            print(f"บันทึก fill ดิบ: {args.dump}")

    api_trades = group_into_trades(fills)

    from analysis.trade_stats import load_records, closed_trades
    try:
        log_trades = closed_trades(load_records(args.log))
    except FileNotFoundError as e:
        print(f"⚠️  {e} — แสดงเฉพาะฝั่ง exchange")
        log_trades = []

    rec = reconcile(api_trades, log_trades)
    print()
    print(format_report(api_trades, rec, balance))


if __name__ == "__main__":
    main()
