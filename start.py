"""
Entry point สำหรับ Railway: รัน bot + dashboard พร้อมกัน

สำคัญ: ทั้งสองตัวเป็น "คนละ process" และมี supervisor คอยปลุกใหม่ถ้าตัวไหนตาย
เหตุผล: 27 ก.ค. 2026 dashboard ตายด้วย Segmentation fault (pandas/pyarrow) แล้ว
process แม่จบตาม → **bot หยุดเทรดทั้งที่ยังถือไม้อยู่** + เว็บขึ้น 502
ไม้ที่ค้างแบบนั้นคือต้นตอของ orphan position / SL-TP ค้าง ที่ทำข้อมูลชุดแรกพัง
→ dashboard ล้ม ต้องไม่ลาก bot ไปด้วย และต้องกลับมาเองโดยไม่ต้องรอ redeploy
"""
import os
import subprocess
import sys
import threading
import time

PORT = os.getenv("PORT", "8501")
RESTART_DELAY = 5          # หน่วงก่อนปลุกใหม่ กัน crash loop ยิงรัวๆ
MAX_RESTARTS = 100         # กันวนไม่รู้จบถ้าพังถาวร (Railway จะ restart container เอง)

BOT_CMD = [sys.executable, "-m", "trading.live_demo"]
DASH_CMD = [
    sys.executable, "-m", "streamlit", "run", "dashboard.py",
    "--server.port", PORT,
    "--server.headless", "true",
    "--server.address", "0.0.0.0",
    # ไม่ต้อง watch ไฟล์บน production — กินแรมกับ inotify handle ฟรีๆ
    "--server.fileWatcherType", "none",
]


def supervise(name: str, cmd: list, stop: threading.Event) -> None:
    """รัน process แล้วปลุกใหม่ถ้าตาย — log ให้เห็นว่าตายเพราะอะไร"""
    for attempt in range(MAX_RESTARTS):
        if stop.is_set():
            return
        started = time.time()
        print(f"[supervisor] เริ่ม {name} (ครั้งที่ {attempt + 1})", flush=True)
        code = subprocess.run(cmd).returncode
        alive = time.time() - started
        print(f"[supervisor] {name} จบด้วย exit={code} หลังรัน {alive:.0f}s "
              f"→ ปลุกใหม่ใน {RESTART_DELAY}s", flush=True)
        time.sleep(RESTART_DELAY)
    print(f"[supervisor] {name} ตายเกิน {MAX_RESTARTS} ครั้ง — ยอมแพ้", flush=True)


if __name__ == "__main__":
    stop = threading.Event()
    bot = threading.Thread(target=supervise, args=("bot", BOT_CMD, stop), daemon=True)
    bot.start()
    # dashboard อยู่ thread หลัก — ถ้าตายก็แค่ปลุกใหม่ ไม่ทำให้ทั้ง container ลง
    supervise("dashboard", DASH_CMD, stop)
