"""ทดสอบ connection ทุกอย่างก่อนรัน main"""
import sys

print("=== ทดสอบ Bybit Testnet ===")
try:
    from data.fetcher import fetch_ohlcv, fetch_ticker
    ticker = fetch_ticker()
    print(f"BTC/USDT ราคาปัจจุบัน: {ticker['last']}")

    df = fetch_ohlcv()
    print(f"ดึง candle สำเร็จ: {len(df)} candles")
    print(f"candle ล่าสุด: {df.index[-1]}")
    print("Binance: OK")
except Exception as e:
    print(f"Binance: FAILED — {e}")
    sys.exit(1)

print()
print("=== ทดสอบ Indicators ===")
try:
    from analysis.indicators import add_indicators
    df = add_indicators(df)
    latest = df.iloc[-1]
    print(f"RSI: {latest['rsi']:.1f}")
    print(f"EMA20: {latest['ema20']:.2f} | EMA50: {latest['ema50']:.2f}")
    print(f"ATR: {latest['atr']:.2f}")
    print("Indicators: OK")
except Exception as e:
    print(f"Indicators: FAILED — {e}")
    sys.exit(1)

print()
print("=== ทดสอบ Signal ===")
try:
    from analysis.signals import generate_signal
    signal = generate_signal(df)
    if signal:
        print(f"พบสัญญาณ: {signal['signal']} @ {signal['price']}")
        print(f"SL: {signal['sl']} | TP: {signal['tp']}")
        print(f"Win Rate: {signal['winrate']}% | Risk: {signal['risk']}")
    else:
        print("ไม่มีสัญญาณตอนนี้ (ปกติ)")
    print("Signal Engine: OK")
except Exception as e:
    print(f"Signal: FAILED — {e}")
    sys.exit(1)

print()
print("=== ทดสอบ Telegram ===")
try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        print("Telegram: SKIP — ยังไม่ได้ตั้งค่า")
    else:
        from alerts.telegram import send_alert
        test_signal = {
            "signal": "LONG",
            "price": 50000.0,
            "sl": 49000.0,
            "tp": 52000.0,
            "rr": 2.0,
            "winrate": 65.0,
            "risk": "LOW",
            "rsi": 48.5,
            "atr": 500.0,
        }
        ok = send_alert(test_signal)
        print(f"Telegram: {'OK — เช็ก chat ได้เลย' if ok else 'FAILED — เช็ก token/chat_id'}")
except Exception as e:
    print(f"Telegram: FAILED — {e}")

print()
print("=== ทุกอย่างพร้อม รัน main.py ได้เลย ===")
