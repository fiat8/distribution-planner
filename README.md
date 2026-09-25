---
title: Distribution Planner
emoji: 🚚
colorFrom: green
colorTo: blue
sdk: streamlit
app_file: app.py
pinned: false
---

# ระบบวางแผนกระจายสินค้า

Master Plan ทุกวันศุกร์ → Monitor รายวัน → Revise → Performance Dashboard
เขียนด้วย Streamlit + Supabase (ดู schema.sql) — engine คำนวณแบบเกลี่ยเปอร์เซ็นต์ (deterministic ไม่มีการสุ่ม) ดู engine.py

## รันบนเครื่องตัวเอง
```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
# ตั้งค่า .streamlit/secrets.toml จาก secrets.toml.example ก่อน
streamlit run app.py
```

## ไฟล์ input ที่ต้องใช้
อัพโหลดในแท็บ "1. Input & คำนวณ" — ไฟล์ Excel ไฟล์เดียว 3 ชีท: `Item Master`, `Demand`, `Stock`
(ดู Master_Input_template.xlsx เป็นตัวอย่างฟอร์แมต)
