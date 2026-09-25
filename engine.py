"""
Distribution planning engine — เกลี่ย demand รายสัปดาห์ลงเป็นเคส/เที่ยวรถรายวัน
Deterministic (ไม่มีการสุ่ม) ตาม requirement ล่าสุด: เกลี่ยตามเปอร์เซ็นต์ที่ปรับเองได้
แล้วปัดขึ้นเป็นจำนวนเที่ยวรถ
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List
import math

DAYS = ["จ", "อ", "พ", "พฤ", "ศ", "ส"]  # index 0..5


def allocate_cases(weekly_qty: float, day_ratio: List[float]) -> List[float]:
    """
    แบ่ง weekly_qty ลง 6 วัน ตามสัดส่วน day_ratio
    ใช้ cumulative-round เพื่อให้ผลรวมตรง weekly_qty เป๊ะ (baseline ต้องตรง demand)
    """
    if len(day_ratio) != 6:
        raise ValueError("day_ratio ต้องมี 6 ค่า (จ..ส)")
    cum_target = 0.0
    cum_alloc = 0.0
    alloc = []
    for r in day_ratio:
        cum_target += weekly_qty * r
        day_alloc = round(cum_target) - cum_alloc
        alloc.append(day_alloc)
        cum_alloc += day_alloc
    return alloc


def cases_to_trips(case_qty: float, cap_per_truck: float) -> int:
    """แปลงเคสเป็นจำนวนเที่ยว ปัดขึ้นเสมอ"""
    if case_qty <= 0:
        return 0
    return math.ceil(case_qty / cap_per_truck)


@dataclass
class PlanDay:
    day: int
    case: float
    trip: int


@dataclass
class Plan:
    demand_id: str
    days: List[PlanDay]

    def total_case(self) -> float:
        return sum(d.case for d in self.days)

    def total_trip(self) -> int:
        return sum(d.trip for d in self.days)


def build_master_plan(demand_id: str, weekly_qty: float, cap_per_truck: float, day_ratio: List[float]) -> Plan:
    cases = allocate_cases(weekly_qty, day_ratio)
    days = [PlanDay(day=d, case=c, trip=cases_to_trips(c, cap_per_truck)) for d, c in enumerate(cases)]
    return Plan(demand_id=demand_id, days=days)


def move_case(plan: Plan, cap_per_truck: float, from_day: int, to_day: int, qty: float) -> Plan:
    """
    ย้ายจำนวน case จาก from_day ไป to_day (เช่น ศุกร์ 500 เคส -> พุธ)
    คำนวณเที่ยวใหม่เฉพาะ 2 วันที่กระทบ วันอื่นไม่แตะ
    """
    if qty <= 0:
        raise ValueError("qty ต้องมากกว่า 0")
    from_pd = plan.days[from_day]
    to_pd = plan.days[to_day]
    if qty > from_pd.case:
        raise ValueError(f"ย้ายได้ไม่เกินจำนวนที่มีในวัน {DAYS[from_day]} ({from_pd.case} เคส)")
    from_pd.case -= qty
    to_pd.case += qty
    from_pd.trip = cases_to_trips(from_pd.case, cap_per_truck)
    to_pd.trip = cases_to_trips(to_pd.case, cap_per_truck)
    return plan


if __name__ == "__main__":
    demo_ratio = [0.2, 0.2, 0.2, 0.2, 0.2, 0.0]
    weekly_qty = 41774
    plan = build_master_plan("DEMO-001", weekly_qty=weekly_qty, cap_per_truck=2520, day_ratio=demo_ratio)
    print("Master Plan baseline:")
    for pd in plan.days:
        print(f"  {DAYS[pd.day]}: {pd.case:.0f} เคส -> {pd.trip} เที่ยว")
    print(f"  รวม: {plan.total_case():.0f} เคส (ต้องตรง {weekly_qty}) / {plan.total_trip()} เที่ยว")
    assert plan.total_case() == weekly_qty, "baseline ต้องตรง demand เป๊ะ"

    print("\nปรับแผน: ย้าย 500 เคส จากศุกร์ (day 4) ไปพุธ (day 2)")
    move_case(plan, cap_per_truck=2520, from_day=4, to_day=2, qty=500)
    for pd in plan.days:
        print(f"  {DAYS[pd.day]}: {pd.case:.0f} เคส -> {pd.trip} เที่ยว")
    print(f"  รวม: {plan.total_case():.0f} เคส (ต้องยังตรง {weekly_qty}) / {plan.total_trip()} เที่ยว")
    assert plan.total_case() == weekly_qty
