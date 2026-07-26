"""
วิเคราะห์สถิติ demo trades ที่เก็บจริงจาก live_demo (forward validation)

ต่างจาก backtest: ตัวนี้อ่าน "ผลเทรดจริง" ที่บันทึกไว้ (trade_log.json บน Railway Volume
หรือ CSV ที่ export จาก dashboard) แล้วคำนวณ metrics ชุดเดียวกับ backtest
→ เทียบ demo vs backtest ได้ตรงๆ ว่า edge ที่เจอใน backtest มาจริงไหม

ใช้ backtest.metrics.compute_metrics ตัวเดียวกับ backtest (นิยาม winrate/PF/Sharpe/
expectancy เหมือนกันเป๊ะ) → ไม่มีปัญหา "เทียบคนละสูตร"

รัน:
    py -3.12 -m analysis.trade_stats                      # อ่าน trade_log.json ตาม DATA_DIR
    py -3.12 -m analysis.trade_stats --file demo.csv      # อ่าน CSV ที่ export จาก dashboard
    py -3.12 -m analysis.trade_stats --json out.json      # เก็บผลเป็น JSON
"""
import argparse
import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime

from backtest.metrics import compute_metrics

# ไม้ที่ MFE/MAE ถึง 70% ของระยะ TP/SL = "เกือบชน" (ตรงกับ threshold ใน backtest.metrics)
NEAR_THRESHOLD = 0.7


# ============================================================
# Load
# ============================================================
def load_records(path: str = None) -> list:
    """
    อ่าน trade records จาก JSON (trade_log.json) หรือ CSV (export จาก dashboard)
    path=None → ใช้ TRADE_LOG ตาม config.DATA_DIR
    """
    if path is None:
        from trading.executor import TRADE_LOG
        path = TRADE_LOG
    if not os.path.exists(path):
        raise FileNotFoundError(f"ไม่พบไฟล์ trade log: {path}")

    if path.lower().endswith(".csv"):
        import pandas as pd
        df = pd.read_csv(path)
        return df.to_dict("records")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def closed_trades(records: list) -> list:
    """
    เอาเฉพาะไม้ที่ปิดแล้ว (มี pnl_pct) — ตัด record ของ DRY_RUN ที่ log ตอนเปิด
    (executor._append_log เขียนทั้ง DRY_RUN entry และ closed trade ลงไฟล์เดียวกัน)
    """
    out = []
    for r in records:
        if r.get("pnl_pct") is None:
            continue
        if str(r.get("mode", "")).upper() == "DRY_RUN":
            continue
        out.append(r)
    out.sort(key=lambda r: str(r.get("entry_time", "")))
    return out


# ============================================================
# Adapter: demo record → schema ที่ compute_metrics ใช้
# ============================================================
def _r_multiple(t: dict) -> float:
    """PnL คิดเป็นกี่ R (1R = ระยะ entry→SL) — เทียบไม้ข้ามช่วงที่ ATR ต่างกันได้"""
    sl_dist = abs(float(t["entry"]) - float(t["sl"]))
    if sl_dist <= 0:
        return 0.0
    return (float(t["pnl_pct"]) * float(t["entry"])) / sl_dist


def to_metric_trades(trades: list) -> list:
    """
    map demo trade → schema ของ backtest trade

    demo เก็บ mfe_pct/mae_pct เป็นสัดส่วนของ "ราคา entry"
    backtest เก็บ mfe_pct_of_tp / mae_pct_of_sl = สัดส่วนของ "ระยะทางถึง TP/SL"
    → แปลงหน่วย: (pct × entry) / distance
    """
    out = []
    for t in trades:
        entry = float(t["entry"])
        sl_dist = abs(entry - float(t["sl"]))
        tp_dist = abs(float(t["tp"]) - entry)
        mfe_dist = abs(float(t.get("mfe_pct", 0.0))) * entry
        mae_dist = abs(float(t.get("mae_pct", 0.0))) * entry
        out.append({
            "entry_time": t.get("entry_time"), "exit_time": t.get("exit_time"),
            "side": t.get("action"), "entry": entry, "exit": t.get("exit"),
            "sl": t.get("sl"), "tp": t.get("tp"),
            "pnl_pct": float(t["pnl_pct"]),
            "result": "win" if float(t["pnl_pct"]) > 0 else "loss",
            "exit_reason": t.get("exit_reason"),
            "mfe_pct_of_tp": round(mfe_dist / tp_dist, 3) if tp_dist > 0 else 0.0,
            "mae_pct_of_sl": round(mae_dist / sl_dist, 3) if sl_dist > 0 else 0.0,
        })
    return out


# ============================================================
# Param eras — ดึงจาก git history ของ active_params.json
# ============================================================
def param_eras(repo_dir: str = None) -> list:
    """
    ไทม์ไลน์ว่า params ชุดไหน live ตั้งแต่เมื่อไหร่ (จาก git log ของ active_params.json)

    ใช้แบ่งไม้ demo ตาม "strategy ที่รันอยู่ตอนนั้น" — ไม้เก่าที่รัน params คนละชุด
    ไม่ควรเอามาตัดสิน strategy ปัจจุบัน

    คืน [{"date", "commit", "subject", "params"}] เรียงเก่า→ใหม่ ([] ถ้าไม่มี git)
    หมายเหตุ: ใช้ commit date — Railway redeploy หลัง push ไม่กี่นาที ถือว่าใกล้พอ
    """
    repo_dir = repo_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rel = "strategy/active_params.json"
    try:
        log = subprocess.run(
            ["git", "log", "--reverse", "--format=%H|%cI|%s", "--", rel],
            cwd=repo_dir, capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return []

    eras = []
    for line in log.splitlines():
        if not line.strip():
            continue
        sha, date, subject = line.split("|", 2)
        try:
            blob = subprocess.run(
                ["git", "show", f"{sha}:{rel}"],
                cwd=repo_dir, capture_output=True, text=True, timeout=30, check=True,
            ).stdout
            params = json.loads(blob)
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
            continue
        eras.append({"date": date, "commit": sha[:7], "subject": subject, "params": params})
    return eras


def _parse_time(v) -> datetime:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def split_by_era(trades: list, eras: list) -> list:
    """แบ่งไม้ตาม era ของ params → [{"era", "trades"}] (เฉพาะ era ที่มีไม้)"""
    if not eras:
        return [{"era": None, "trades": trades}]

    bounds = []
    for e in eras:
        d = _parse_time(e["date"])
        if d:
            bounds.append((d, e))
    bounds.sort(key=lambda x: x[0])

    buckets = defaultdict(list)
    for t in trades:
        ts = _parse_time(t.get("entry_time"))
        idx = None
        if ts is not None:
            for i, (d, _) in enumerate(bounds):
                if ts >= d:
                    idx = i
        buckets[idx].append(t)

    out = []
    for idx in sorted(buckets, key=lambda i: (i is None, i)):
        era = bounds[idx][1] if idx is not None else None
        out.append({"era": era, "trades": buckets[idx]})
    return out


# ============================================================
# Breakdown / diagnostics
# ============================================================
def breakdown(trades: list, key_fn, label: str) -> dict:
    """สถิติแยกตามกลุ่ม (ทิศทาง / confluence / ช่วงเวลา) — ดูว่ากลุ่มไหนกินกำไร"""
    groups = defaultdict(list)
    for t in trades:
        k = key_fn(t)
        if k is not None:
            groups[k].append(t)

    rows = {}
    for k, ts in groups.items():
        pnls = [float(t["pnl_pct"]) for t in ts]
        wins = [p for p in pnls if p > 0]
        rows[str(k)] = {
            "trades": len(ts),
            "winrate": round(len(wins) / len(ts) * 100, 1),
            "total_pnl_pct": round(sum(pnls) * 100, 2),
            "avg_pnl_pct": round(sum(pnls) / len(ts) * 100, 3),
            "avg_r": round(sum(_r_multiple(t) for t in ts) / len(ts), 2),
        }
    return {"label": label, "groups": rows}


def exit_management_whatif(trades: list) -> dict:
    """
    ประเมินคร่าวๆ ว่า breakeven / trailing จะช่วยไหม — จาก MFE/MAE ของไม้จริง

    ⚠️ เป็น "ขอบบน" ไม่ใช่ผลจริง: MFE/MAE ไม่ได้บอกว่าอะไรเกิดก่อน
    (ไม้ที่เคยบวก 1R แล้วแพ้ อาจลงไปแตะ SL ก่อนขึ้นก็ได้) → ใช้ชี้เป้าว่าน่าไปทดสอบ
    ใน backtest engine (ที่มี bar data จริง) ต่อ ไม่ใช้ตัดสินใจเปิด feature เอง
    """
    losses = [t for t in trades if float(t["pnl_pct"]) <= 0]
    wins = [t for t in trades if float(t["pnl_pct"]) > 0]
    if not trades:
        return {}

    # ไม้แพ้ที่เคยกำไรถึง 1R / 0.5R (BE หรือ trail จะเซฟไว้ได้ถ้าจังหวะเป็นใจ)
    loss_reached_1r = [t for t in losses if _mfe_r(t) >= 1.0]
    loss_reached_05r = [t for t in losses if _mfe_r(t) >= 0.5]
    # ไม้ชนะที่เคยลบลึก ≥70% ของ SL (BE ที่ตั้งเร็วไปจะตัดไม้พวกนี้ทิ้ง)
    win_deep_mae = [t for t in wins if abs(float(t.get("mae_pct", 0.0))) * float(t["entry"])
                    >= NEAR_THRESHOLD * abs(float(t["entry"]) - float(t["sl"]))]

    return {
        "losses": len(losses),
        "loss_reached_1r": len(loss_reached_1r),
        "loss_reached_0.5r": len(loss_reached_05r),
        "wins": len(wins),
        "wins_with_deep_mae": len(win_deep_mae),
        "note": "ขอบบน (upper bound) — MFE/MAE ไม่บอกลำดับก่อนหลัง ต้อง confirm ด้วย backtest engine",
    }


def _mfe_r(t: dict) -> float:
    """MFE คิดเป็นกี่ R"""
    sl_dist = abs(float(t["entry"]) - float(t["sl"]))
    if sl_dist <= 0:
        return 0.0
    return abs(float(t.get("mfe_pct", 0.0))) * float(t["entry"]) / sl_dist


def data_quality(records: list, trades: list) -> dict:
    """
    เช็คความน่าเชื่อของข้อมูลก่อนเอาไปสรุป

    exit == entry = record_closed_trade() ดึง exit price จาก fills ไม่ได้ (testnet 502)
    แล้ว fallback เป็น entry → ไม้นั้นถูกบันทึกเป็น "แพ้ค่า fee" ทั้งที่ผลจริงไม่รู้
    """
    dry = sum(1 for r in records if str(r.get("mode", "")).upper() == "DRY_RUN")
    suspicious = [t for t in trades
                  if t.get("exit") is not None and float(t["exit"]) == float(t["entry"])]
    missing_excursion = [t for t in trades if t.get("mfe_pct") is None or t.get("mae_pct") is None]
    return {
        "total_records": len(records),
        "closed_trades": len(trades),
        "dry_run_records": dry,
        "exit_equals_entry": len(suspicious),
        "missing_mfe_mae": len(missing_excursion),
    }


# ============================================================
# Report
# ============================================================
def analyze(records: list) -> dict:
    trades = closed_trades(records)
    if not trades:
        return {"error": "ไม่มีไม้ที่ปิดแล้วใน log", "quality": data_quality(records, [])}

    overall = compute_metrics(to_metric_trades(trades), {}).metrics
    result = {
        "quality": data_quality(records, trades),
        "period": {
            "first_entry": trades[0].get("entry_time"),
            "last_exit": trades[-1].get("exit_time"),
        },
        "overall": overall,
        "avg_r": round(sum(_r_multiple(t) for t in trades) / len(trades), 3),
        "by_direction": breakdown(trades, lambda t: t.get("action"), "ทิศทาง"),
        "by_exit_reason": breakdown(trades, lambda t: t.get("exit_reason"), "ปิดเพราะ"),
        "by_confluence": breakdown(trades, lambda t: t.get("confluence"), "confluence score"),
        "by_hour": breakdown(
            trades,
            lambda t: (_parse_time(t.get("entry_time")).hour
                       if _parse_time(t.get("entry_time")) else None),
            "ชั่วโมงที่เข้า (เวลา server)",
        ),
        "exit_whatif": exit_management_whatif(trades),
        "eras": [],
    }

    for seg in split_by_era(trades, param_eras()):
        if not seg["trades"]:
            continue
        era = seg["era"]
        p = era["params"] if era else {}
        result["eras"].append({
            "since": era["date"] if era else "ก่อน active_params commit แรก",
            "commit": era["commit"] if era else None,
            "subject": era["subject"] if era else "params ไม่ทราบ (ก่อนเริ่ม track)",
            "filters": {k: p.get(k) for k in
                        ("timeframe", "macd_only", "skip_high_vol", "htf_filter", "confluence_min")},
            "metrics": compute_metrics(to_metric_trades(seg["trades"]), {}).metrics,
        })
    return result


def format_report(a: dict) -> str:
    if a.get("error"):
        q = a["quality"]
        return (f"❌ {a['error']}\n"
                f"   records ทั้งหมด {q['total_records']} (DRY_RUN {q['dry_run_records']})")

    m = a["overall"]
    q = a["quality"]
    L = []
    L.append("=" * 62)
    L.append("  DEMO TRADE STATS — forward validation")
    L.append("=" * 62)
    L.append(f"ช่วง: {a['period']['first_entry']} → {a['period']['last_exit']}")
    L.append(f"ข้อมูล: {q['closed_trades']} ไม้ปิดแล้ว "
             f"(จาก {q['total_records']} records, DRY_RUN {q['dry_run_records']})")
    if q["exit_equals_entry"]:
        L.append(f"⚠️  {q['exit_equals_entry']} ไม้มี exit == entry "
                 f"(ดึงราคาปิดไม่ได้ตอนบันทึก) — ตัวเลขจะเพี้ยนลบเล็กน้อย")
    if q["missing_mfe_mae"]:
        L.append(f"⚠️  {q['missing_mfe_mae']} ไม้ไม่มี MFE/MAE")

    L.append("")
    L.append("── ภาพรวม ─────────────────────────────────────────────────────")
    L.append(f"  ไม้ทั้งหมด      {m['num_trades']}")
    L.append(f"  Winrate         {m['winrate']:.1f}%")
    L.append(f"  Total return    {m['total_return']*100:+.2f}%")
    L.append(f"  Expectancy      {m['expectancy']*100:+.3f}% ต่อไม้   (avg {a['avg_r']:+.2f}R)")
    L.append(f"  Profit factor   {m['profit_factor']:.2f}"
             f"      {'✅ >1 = มี edge' if m['profit_factor'] > 1 else '❌ <1 = ยังขาดทุน'}")
    L.append(f"  Max drawdown    {m['max_drawdown']*100:.2f}%")
    L.append(f"  Sharpe          {m['sharpe']:.2f}")
    L.append(f"  Avg win/loss    {m['avg_win']*100:+.2f}% / {m['avg_loss']*100:+.2f}%")
    L.append(f"  แพ้ติดกันสูงสุด  {m['max_consecutive_losses']} ไม้")

    L.append("")
    L.append("── MFE/MAE (ตั้ง SL/TP เหมาะไหม) ──────────────────────────────")
    L.append(f"  ไม้แพ้ เคยกำไรเฉลี่ย {m['avg_mfe_loss']*100:.0f}% ของระยะ TP")
    L.append(f"  ไม้ชนะ เคยขาดทุนเฉลี่ย {m['avg_mae_win']*100:.0f}% ของระยะ SL")
    L.append(f"  แพ้ทั้งที่เกือบชน TP (≥70%): {m['near_tp_miss_pct']:.0f}% ของไม้แพ้")
    L.append(f"  ชนะทั้งที่เกือบโดน SL (≥70%): {m['near_sl_scare_pct']:.0f}% ของไม้ชนะ")

    w = a["exit_whatif"]
    if w:
        L.append("")
        L.append("── ถ้าใช้ BE/trailing จะช่วยไหม (ขอบบน) ───────────────────────")
        L.append(f"  ไม้แพ้ที่เคยกำไรถึง 1R:   {w['loss_reached_1r']}/{w['losses']}")
        L.append(f"  ไม้แพ้ที่เคยกำไรถึง 0.5R: {w['loss_reached_0.5r']}/{w['losses']}")
        L.append(f"  ไม้ชนะที่เคยลบลึก ≥70% SL: {w['wins_with_deep_mae']}/{w['wins']} "
                 f"(BE เร็วไปจะตัดกลุ่มนี้ทิ้ง)")
        L.append(f"  {w['note']}")

    for name in ("by_direction", "by_exit_reason", "by_confluence", "by_hour"):
        b = a[name]
        if not b["groups"]:
            continue
        L.append("")
        L.append(f"── แยกตาม {b['label']} ─────────────────────────────────────")
        L.append(f"  {'กลุ่ม':<12}{'ไม้':>5}{'winrate':>10}{'รวม PnL':>11}{'avg R':>8}")
        for k, r in sorted(b["groups"].items(), key=lambda kv: -kv[1]["total_pnl_pct"]):
            L.append(f"  {k:<12}{r['trades']:>5}{r['winrate']:>9.0f}%"
                     f"{r['total_pnl_pct']:>+10.2f}%{r['avg_r']:>+8.2f}")

    if a["eras"]:
        L.append("")
        L.append("── แยกตามชุด params ที่รันอยู่ตอนนั้น ─────────────────────────")
        L.append("  (ไม้เก่าที่รัน params คนละชุด ไม่ควรเอามาตัดสิน strategy ปัจจุบัน)")
        for e in a["eras"]:
            em = e["metrics"]
            f = e["filters"]
            L.append("")
            L.append(f"  ▸ ตั้งแต่ {str(e['since'])[:10]} — {e['subject']}")
            L.append(f"    filters: tf={f.get('timeframe')} macd_only={f.get('macd_only')} "
                     f"skip_high_vol={f.get('skip_high_vol')} conf_min={f.get('confluence_min')}")
            L.append(f"    {em['num_trades']} ไม้ | win {em['winrate']:.0f}% | "
                     f"return {em['total_return']*100:+.2f}% | PF {em['profit_factor']:.2f} | "
                     f"expect {em['expectancy']*100:+.3f}%")

    L.append("")
    L.append("=" * 62)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="วิเคราะห์สถิติ demo trades ที่เก็บจริง")
    ap.add_argument("--file", help="path ของ trade_log.json หรือ CSV ที่ export จาก dashboard")
    ap.add_argument("--json", help="เขียนผลวิเคราะห์เป็น JSON ที่ path นี้")
    args = ap.parse_args()

    records = load_records(args.file)
    a = analyze(records)
    print(format_report(a))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(a, f, indent=2, ensure_ascii=False)
        print(f"\nบันทึก JSON: {args.json}")


if __name__ == "__main__":
    main()
