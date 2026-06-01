"""
Entry point สำหรับ Railway: รัน bot + dashboard พร้อมกัน
"""
import subprocess
import sys
import os
import threading

PORT = os.getenv("PORT", "8501")


def run_bot():
    subprocess.run([sys.executable, "main.py"])


def run_dashboard():
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", "dashboard.py",
        "--server.port", PORT,
        "--server.headless", "true",
        "--server.address", "0.0.0.0",
    ])


if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    run_dashboard()
