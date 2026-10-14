# records/ — ข้อมูลเทรดจริงของ live demo bot

ผลเทรดจริงทั้งหมดของบอทบน **Binance USD-M futures testnet** (BTC/USDT, 15m)
ตั้งแต่ 1 มิ.ย. 2026 — เก็บเข้า git ไว้เพราะ:

- **ข้อมูลบน exchange มีอายุ:** order ปกติ query ได้แค่ **90 วัน** (error `-4166`), fill ได้ **6 เดือน**, ประวัติ SL/TP ที่ยังเหลือเริ่มที่ 12 มิ.ย.
- **เลิก deploy Railway (ต.ค. 2026)** → `trade_log.json` บน volume หายตามไป ไฟล์ในนี้คือหลักฐานที่เหลืออยู่

## ไฟล์

| ไฟล์ | คืออะไร | key |
|---|---|---|
| `binance_fills.json` | fill ดิบทุกรายการ (ground truth: ราคา, ขนาด, `realized_pnl`, `commission` จาก Binance) | `trade_id` |
| `binance_algo_orders.json` | SL/TP (conditional order): `trigger`, `status`, **`actual_price` = ราคาที่ fill จริงหลัง trigger**, `reject_reason` | `algo_id` |
| `binance_orders.json` | order ปกติ (market entry / บอทปิดเอง) — มีแค่ 90 วันล่าสุดตอน export | `order_id` |
| `trades.csv` | **ไม้ที่ประกอบแล้ว 1 แถว/ไม้** (สร้างจาก 3 ไฟล์บน — สร้างใหม่ได้เสมอ) | — |
| `meta.json` | เวลา export, balance ณ ตอนนั้น, ช่วงข้อมูล, สรุปแยกยุค | — |

### คอลัมน์ `trades.csv`

| คอลัมน์ | ความหมาย |
|---|---|
| `era` | ยุคของโค้ดที่ live ตอนเปิดไม้ (ดูด้านล่าง) — **ห้ามรวมต่างยุคมาวัดผล** |
| `entry` / `exit` | ราคาเฉลี่ยถ่วงขนาด (VWAP) ของ fill ขาเข้า/ขาออก |
| `gross_usdt` / `fee_usdt` / `net_usdt` | PnL จาก Binance โดยตรง (ไม่ได้คำนวณเอง) |
| `gross_pct` / `net_pct` | PnL เป็น % ของ notional ขาเข้า |
| `sl` / `tp` | trigger price ของ SL/TP ที่ตั้งจริง |
| `sl_pct` | ระยะ SL เป็น % ของราคา (= 1R) |
| `r_multiple` | ราคาวิ่งได้กี่ R (ก่อน fee) |
| `fee_r` | ค่าธรรมเนียมคิดเป็นกี่ R |
| `exit_via` | ไม้จบด้วยอะไร: `SL` · `TP` · `bot_market` (บอทปิดเอง) · `no_stops` (ตั้ง SL/TP ไม่ผ่าน) · `stale_order` (โดน SL/TP ค้างของไม้ก่อนปิด — บั๊กยุค E0) · `no_history` (เก่ากว่าประวัติ SL/TP ที่เหลือ = **ไม่รู้ ไม่ใช่บั๊ก**) |
| `exit_slip_bps` | ราคาที่ fill จริงแย่กว่า trigger กี่ bps (บวก = เสียเปรียบ) |

## ยุค

| ยุค | เริ่ม | โค้ดที่ live | ใช้วัด strategy ได้? |
|---|---|---|---|
| `E0_buggy` | 1 มิ.ย. | execution มีบั๊ก exit price / phantom close / SL-TP ค้าง | ❌ |
| `E1_exit_fixed` | 28 ก.ค. 04:30 | แก้บั๊ก exit + supervisor | ⚠️ ไม้ขยะ 6/27 จาก SL แคบ |
| `E2_sl_floor` | 16 ส.ค. 04:31 | + พื้น SL 0.15% + เพดาน notional 8x | ✅ ชุดที่สะอาดที่สุด |

## อัปเดต / สร้างใหม่

```bash
py -3.12 -m analysis.export_records            # ดึงจาก exchange + merge (ของเดิมไม่หาย)
py -3.12 -m analysis.export_records --offline  # สร้าง trades.csv + สรุปใหม่จากไฟล์อย่างเดียว
```

## ข้อควรระวัง

- **testnet ≠ ของจริง:** order book บาง → slippage ตอน TP trigger (median ~10 bps) น่าจะมากกว่า mainnet
- SL/TP ทุกตัว trigger ด้วย `CONTRACT_PRICE` (ราคาเทรดล่าสุด) ไม่ใช่ mark price
- ไม่มี metadata ของ signal (confluence, MFE/MAE, ราคา signal) — อยู่ใน `trade_log.json` บน Railway ถ้า export มาได้ให้วางไว้ในนี้เป็น `trade_log.json`
