import asyncio
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SYMBOL, TIMEFRAME


RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}


def format_message(signal: dict) -> str:
    emoji = "📈" if signal["signal"] == "LONG" else "📉"
    risk_icon = RISK_EMOJI.get(signal["risk"], "⚪")

    return (
        f"{emoji} *{signal['signal']} Signal — {SYMBOL}* ({TIMEFRAME})\n\n"
        f"💰 Entry: `{signal['price']}`\n"
        f"🛑 SL: `{signal['sl']}`\n"
        f"🎯 TP: `{signal['tp']}`\n"
        f"⚖️ R:R = 1:{signal['rr']}\n\n"
        f"📊 Win Rate (est.): *{signal['winrate']}%*\n"
        f"{risk_icon} Risk: *{signal['risk']}*\n\n"
        f"RSI: {signal['rsi']} | ATR: {signal['atr']}"
    )


def send_alert(signal: dict) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] ยังไม่ได้ตั้งค่า BOT_TOKEN หรือ CHAT_ID")
        return False

    message = format_message(signal)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    })
    return resp.status_code == 200
