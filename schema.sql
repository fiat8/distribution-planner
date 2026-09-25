-- Distribution Planning & Performance Dashboard
-- Schema สำหรับ Supabase (Postgres) — เวอร์ชัน single-user (ไม่มี auth/role table)
-- Engine คำนวณแบบเกลี่ยเปอร์เซ็นต์ (deterministic) ดู engine.py คู่กัน

create extension if not exists "pgcrypto";  -- สำหรับ gen_random_uuid()

-- ============================================================
-- 1) Item Master: Material Code -> ความจุต่อเที่ยว (Cap/Truck)
-- ============================================================
create table item_master (
    item_id        text primary key,
    description    text not null,
    cap_per_truck  numeric not null check (cap_per_truck > 0)
);

-- ============================================================
-- 2) Plant Master: PLANT CODE -> ชื่อคลัง/โรงงาน
-- ============================================================
create table plant_master (
    plant_code  text primary key,
    plant_name  text not null
);

-- ============================================================
-- 3) Demand: ยอดต้องการต่อสัปดาห์ ต่อสินค้า ต่อต้นทาง-ปลายทาง
--    origin_plant / destination เก็บเป็น PLANT CODE (join กับ plant_master เพื่อโชว์ชื่อ)
--    day_ratio ปรับได้จากหน้า UI (default เกลี่ยเท่ากันทุกวัน active)
-- ============================================================
create table demand (
    demand_id     uuid primary key default gen_random_uuid(),
    item_id       text not null references item_master(item_id),
    origin_plant  text not null,
    destination   text not null,
    week_id       text not null,                         -- เช่น 'W40'
    weekly_qty    numeric not null check (weekly_qty >= 0),
    day_ratio     numeric[] not null default '{20,20,20,20,20,0}',  -- [จ,อ,พ,พฤ,ศ,ส] เก็บเป็น 0-100
    created_at    timestamptz default now(),
    unique (item_id, destination, week_id)
);

-- ============================================================
-- 4) Master Plan: baseline หลัง publish — ห้ามแก้ทับ
-- ============================================================
create table master_plan (
    plan_id       uuid primary key default gen_random_uuid(),
    demand_id     uuid not null references demand(demand_id) on delete cascade,
    day_of_week   smallint not null check (day_of_week between 0 and 5),  -- 0=จ..5=ส
    planned_case  numeric not null,
    planned_trip  integer not null,
    published_at  timestamptz default now(),
    unique (demand_id, day_of_week)
);

-- ============================================================
-- 5) Revised Plan: การปรับแผน อ้างอิงกลับ master_plan เสมอ
--    เช่น ย้าย 500 เคส จากศุกร์ไปพุธ = insert 2 แถว (วันละแถว)
-- ============================================================
create table revised_plan (
    revision_id   uuid primary key default gen_random_uuid(),
    plan_id       uuid not null references master_plan(plan_id) on delete cascade,
    day_of_week   smallint not null check (day_of_week between 0 and 5),
    revised_case  numeric not null,
    revised_trip  integer not null,
    reason        text not null,
    revised_at    timestamptz default now()
);

-- ============================================================
-- 6) Actual Delivery: นำเข้าจากไฟล์ STO เพื่อเช็ค performance
-- ============================================================
create table actual_delivery (
    delivery_id   uuid primary key default gen_random_uuid(),
    demand_id     uuid not null references demand(demand_id) on delete cascade,
    delivery_date date not null,
    actual_case   numeric not null default 0,
    actual_trip   integer not null default 0,
    sto_ref       text,
    created_at    timestamptz default now()
);

-- ============================================================
-- 7) Stock ต้นทาง (snapshot ตามวันที่อัพโหลด)
-- ============================================================
create table stock_snapshot (
    item_id        text not null references item_master(item_id) on delete cascade,
    snapshot_date  date not null,
    available_qty  numeric not null,
    primary key (item_id, snapshot_date)
);

-- ดูแผนล่าสุด (master + revision ล่าสุดต่อวัน) ในคิวรีเดียว ใช้เลี้ยงหน้า Monitor/Dashboard
create view v_current_plan as
select
    mp.demand_id,
    mp.day_of_week,
    mp.planned_case,
    mp.planned_trip,
    coalesce(rp.revised_case, mp.planned_case) as current_case,
    coalesce(rp.revised_trip, mp.planned_trip) as current_trip
from master_plan mp
left join lateral (
    select revised_case, revised_trip
    from revised_plan
    where plan_id = mp.plan_id and day_of_week = mp.day_of_week
    order by revised_at desc
    limit 1
) rp on true;

-- Supabase project นี้เปิด RLS อัตโนมัติให้ตารางใหม่ทุกตัว (เจอปัญหานี้มาแล้ว 2 รอบ) —
-- ปิดไว้ทั้งหมดเพราะเป็นแอพคนเดียวใช้ ไม่ต้องมี row-level access control
alter table item_master disable row level security;
alter table plant_master disable row level security;
alter table demand disable row level security;
alter table master_plan disable row level security;
alter table revised_plan disable row level security;
alter table actual_delivery disable row level security;
alter table stock_snapshot disable row level security;
