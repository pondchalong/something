import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SYMBOL, TIMEFRAME

RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}


def _send(text: str) -> bool:
    """ส่ง Markdown message ไป Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] ยังไม่ได้ตั้งค่า BOT_TOKEN หรือ CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram] ส่งไม่ได้: {e}")
        return False


def format_message(signal: dict) -> str:
    emoji = "📈" if signal["signal"] == "LONG" else "📉"
    risk_icon = RISK_EMOJI.get(signal.get("risk"), "⚪")
    return (
        f"{emoji} *{signal['signal']} Signal — {SYMBOL}* ({TIMEFRAME})\n\n"
        f"💰 Entry: `{signal['price']}`\n"
        f"🛑 SL: `{signal['sl']}`\n"
        f"🎯 TP: `{signal['tp']}`\n"
        f"⚖️ R:R = 1:{signal['rr']}\n\n"
        f"📊 Win Rate (est.): *{signal['winrate']}%*\n"
        f"{risk_icon} Risk: *{signal['risk']}*\n"
        f"🔢 Confluence: {signal.get('confluence', '?')}/8\n\n"
        f"RSI: {signal.get('rsi', '?')} | ATR: {signal.get('atr', '?')}"
    )


def send_alert(signal: dict) -> bool:
    """แจ้งเตือนตอนเปิดไม้ / มีสัญญาณ"""
    return _send(format_message(signal))


def send_closed_alert(closed: dict) -> bool:
    """แจ้งเตือนตอนไม้ปิด (SL/TP/reverse) พร้อมผล win/loss + MFE/MAE"""
    won = closed.get("won")
    emoji = "✅" if won else "❌"
    res = "WIN" if won else "LOSS"
    dur = closed.get("duration_min")
    dur_txt = f"{dur} นาที" if dur is not None else "?"
    return _send(
        f"{emoji} *ปิดไม้ {res}* — {closed.get('symbol', SYMBOL)}\n\n"
        f"{closed['action']} | entry `{closed['entry']}` → exit `{closed['exit']}`\n"
        f"ปิดเพราะ: *{closed['exit_reason']}*\n"
        f"PnL: *{closed['pnl_pct']*100:+.2f}%*\n"
        f"MFE {closed['mfe_pct']*100:+.2f}% / MAE {closed['mae_pct']*100:+.2f}%\n"
        f"⏱ ถือ {dur_txt}"
    )


def send_skip_alert(signal: dict, holding_side: str) -> bool:
    """แจ้งเตือนเมื่อมี signal แต่ถือไม้อยู่ → ข้าม (ไม่เปิดไม้ใหม่)"""
    emoji = "📈" if signal["signal"] == "LONG" else "📉"
    return _send(
        f"⏭️ *Signal {signal['signal']} (ข้าม)* — {SYMBOL}\n\n"
        f"{emoji} signal ที่ `{signal['price']}` (confluence {signal.get('confluence', '?')}/8)\n"
        f"กำลังถือ *{holding_side}* อยู่ → ไม่เปิดไม้ใหม่ (1-position)"
    )
