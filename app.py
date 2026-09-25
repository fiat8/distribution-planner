"""
ระบบวางแผนกระจายสินค้า — Streamlit app
รวม 4 หน้าตาม mock up: Input & คำนวณ / Dashboard / Monitor-Edit / Transactions
ต่อกับ Supabase (schema.sql) และ engine.py (deterministic allocation, ไม่มีการสุ่ม)
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st
from supabase import Client, create_client

from engine import DAYS, allocate_cases, cases_to_trips

st.set_page_config(page_title="My Distributions' Plan", layout="wide")

DAY_LABELS = DAYS  # ["จ","อ","พ","พฤ","ศ","ส"]


# ============================================================
# Supabase client + DB helpers
# ============================================================
@st.cache_resource
def get_client() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


def default_week_start() -> dt.date:
    """วันจันทร์ถัดไป — ใช้เป็นค่าเริ่มต้นตอนวางแผนวันศุกร์สำหรับสัปดาห์หน้า"""
    today = dt.date.today()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + dt.timedelta(days=days_ahead)


def default_week_number() -> int:
    return default_week_start().isocalendar()[1]


@st.cache_data(ttl=30)
def fetch_items() -> pd.DataFrame:
    res = get_client().table("item_master").select("*").order("item_id").execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=30)
def fetch_plants() -> pd.DataFrame:
    res = get_client().table("plant_master").select("*").order("plant_code").execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=30)
def fetch_demand(week_id: str | None = None) -> pd.DataFrame:
    q = get_client().table("demand").select("*")
    if week_id:
        q = q.eq("week_id", week_id)
    res = q.order("item_id").execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=30)
def fetch_stock() -> pd.DataFrame:
    res = get_client().table("stock_snapshot").select("*").order("snapshot_date", desc=True).execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=15)
def fetch_week_ids() -> list[str]:
    res = get_client().table("demand").select("week_id").execute()
    weeks = sorted({r["week_id"] for r in res.data}, reverse=True)
    return weeks


@st.cache_data(ttl=15)
def fetch_master_plan_rows(demand_ids: list[str]) -> pd.DataFrame:
    if not demand_ids:
        return pd.DataFrame(columns=["plan_id", "demand_id", "day_of_week", "planned_case", "planned_trip"])
    res = get_client().table("master_plan").select("*").in_("demand_id", demand_ids).execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=15)
def fetch_current_plan_rows(demand_ids: list[str]) -> pd.DataFrame:
    """ใช้ view v_current_plan: master ผสม revision ล่าสุดต่อวัน"""
    if not demand_ids:
        return pd.DataFrame(columns=["demand_id", "day_of_week", "current_case", "current_trip"])
    res = get_client().table("v_current_plan").select("*").in_("demand_id", demand_ids).execute()
    return pd.DataFrame(res.data)


@st.cache_data(ttl=15)
def fetch_transactions(demand_ids: list[str]) -> pd.DataFrame:
    if not demand_ids:
        return pd.DataFrame()
    res = (
        get_client()
        .table("revised_plan")
        .select("*, master_plan!inner(demand_id)")
        .execute()
    )
    rows = [r for r in res.data if r["master_plan"]["demand_id"] in demand_ids]
    return pd.DataFrame(rows)


@st.cache_data(ttl=15)
def fetch_actual(demand_ids: list[str]) -> pd.DataFrame:
    if not demand_ids:
        return pd.DataFrame(columns=["demand_id", "delivery_date", "actual_case", "actual_trip"])
    res = get_client().table("actual_delivery").select("*").in_("demand_id", demand_ids).execute()
    return pd.DataFrame(res.data)


def clear_caches():
    fetch_items.clear()
    fetch_plants.clear()
    fetch_demand.clear()
    fetch_stock.clear()
    fetch_week_ids.clear()
    fetch_master_plan_rows.clear()
    fetch_current_plan_rows.clear()
    fetch_transactions.clear()
    fetch_actual.clear()


def upsert_items(df: pd.DataFrame):
    records = df[["item_id", "description", "cap_per_truck"]].to_dict("records")
    get_client().table("item_master").upsert(records, on_conflict="item_id").execute()


def upsert_plants(df: pd.DataFrame):
    records = df[["plant_code", "plant_name"]].to_dict("records")
    get_client().table("plant_master").upsert(records, on_conflict="plant_code").execute()


def upsert_demand(df: pd.DataFrame, week_id: str):
    df = df.copy()
    df["week_id"] = week_id
    df["day_ratio"] = [[20, 20, 20, 20, 20, 0]] * len(df)  # default เท่ากันทุกวัน active — ปรับได้ในแอพ
    records = df[["item_id", "origin_plant", "destination", "week_id", "weekly_qty", "day_ratio"]].to_dict("records")
    get_client().table("demand").upsert(records, on_conflict="item_id,destination,week_id").execute()


def upsert_stock(df: pd.DataFrame):
    records = df[["item_id", "snapshot_date", "available_qty"]].to_dict("records")
    get_client().table("stock_snapshot").upsert(records, on_conflict="item_id,snapshot_date").execute()


def update_demand_ratio(demand_id: str, ratio_pct: list[int]):
    get_client().table("demand").update({"day_ratio": ratio_pct}).eq("demand_id", demand_id).execute()


def demand_has_master_plan(demand_id: str) -> bool:
    res = get_client().table("master_plan").select("plan_id").eq("demand_id", demand_id).limit(1).execute()
    return len(res.data) > 0


def insert_master_plan(demand_id: str, alloc: list[float], trip: list[int]):
    rows = [
        {"demand_id": demand_id, "day_of_week": d, "planned_case": alloc[d], "planned_trip": trip[d]}
        for d in range(6)
    ]
    get_client().table("master_plan").insert(rows).execute()


def insert_revision(plan_id: str, day_of_week: int, revised_case: float, revised_trip: int, reason: str):
    get_client().table("revised_plan").insert(
        {
            "plan_id": plan_id,
            "day_of_week": day_of_week,
            "revised_case": revised_case,
            "revised_trip": revised_trip,
            "reason": reason,
        }
    ).execute()


def insert_actual(df: pd.DataFrame):
    records = df[["demand_id", "delivery_date", "actual_case", "actual_trip", "sto_ref"]].to_dict("records")
    get_client().table("actual_delivery").insert(records).execute()


def reset_all():
    """ปุ่ม Reset — ล้างข้อมูลทั้งหมดในเว็บแอปทุกสัปดาห์ (demand จะ cascade ลบ master_plan/revised_plan/
    actual_delivery ให้เอง) รวมถึง Item Master, Plant Master, Stock ด้วย เพื่อไม่ให้มีข้อมูลเก่าค้างในแท็บ/ตัวกรองอื่น"""
    get_client().table("demand").delete().neq("demand_id", "00000000-0000-0000-0000-000000000000").execute()
    get_client().table("item_master").delete().neq("item_id", "").execute()
    get_client().table("plant_master").delete().neq("plant_code", "").execute()
    get_client().table("stock_snapshot").delete().neq("item_id", "").execute()


# ============================================================
# UI
# ============================================================
st.markdown(
    "<h1 style='text-align:center; font-weight:800; text-decoration:underline;'>My Distributions' Plan</h1>",
    unsafe_allow_html=True,
)

st.markdown(
    """
    <style>
    .stTabs [data-baseweb="tab-list"] {
        width: 100%;
        justify-content: center;
    }
    .stTabs [data-baseweb="tab"] {
        flex: 1;
        justify-content: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

tab1, tab2, tab3, tab4 = st.tabs(["Input data", "Dashboard", "Edit Plan", "Transaction"])

# ---------------------------------------------------------------
# TAB 1: Input & คำนวณ
# ---------------------------------------------------------------
with tab1:
    st.subheader("Input Master file")
    st.caption('ไฟล์ Excel ไฟล์เดียว 3 ชีท: "Item Master", "Demand", "Stock" — ตาม Master_Input_template.xlsx')

    wk_col1, wk_col2 = st.columns(2)
    week_number = wk_col1.number_input("Week Number", min_value=1, step=1, value=default_week_number())
    week_start = wk_col2.date_input("Week Start Date (Monday)", value=default_week_start())
    week_id = f"W{int(week_number)}"
    week_dates = [week_start + dt.timedelta(days=i) for i in range(6)]
    day_headers = [f"{d.strftime('%a')} {d.day}/{d.month}" for d in week_dates]

    uploaded = st.file_uploader("Select Master file", type=["xlsx"])
    if uploaded is not None:
        try:
            items_df_new = pd.read_excel(uploaded, sheet_name="Item Master", header=2, dtype={"item_id": str})

            plants_df_new = pd.read_excel(
                uploaded, sheet_name="Plant Code", header=2, dtype={"PLANT CODE": str}
            )
            plants_df_new.columns = [" ".join(str(c).split()) for c in plants_df_new.columns]
            plants_df_new = plants_df_new.rename(columns={"PLANT CODE": "plant_code", "PLANT NAME": "plant_name"})

            demand_raw = pd.read_excel(
                uploaded, sheet_name="Demand", header=2,
                dtype={"PLANT ORIGNS": str, "PLANT DESTINATION": str, "ITEM": str},
            )
            demand_raw.columns = [" ".join(str(c).split()) for c in demand_raw.columns]
            demand_df_new = demand_raw.rename(columns={
                "PLANT ORIGNS": "origin_plant",
                "PLANT DESTINATION": "destination",
                "ITEM": "item_id",
                "Unit Required": "weekly_qty",
            })[["item_id", "origin_plant", "destination", "weekly_qty"]]

            stock_df = pd.read_excel(uploaded, sheet_name="Stock", header=2, dtype={"item_id": str})
            stock_df["snapshot_date"] = pd.to_datetime(stock_df["snapshot_date"]).dt.date.astype(str)

            if st.button("Import", type="primary"):
                upsert_plants(plants_df_new)
                upsert_items(items_df_new)
                upsert_demand(demand_df_new, week_id)
                upsert_stock(stock_df)
                clear_caches()
                st.success(
                    f"นำเข้าเรียบร้อย: {len(plants_df_new)} plants, {len(items_df_new)} items, "
                    f"{len(demand_df_new)} demand, {len(stock_df)} stock"
                )
                st.rerun()
        except Exception as e:  # noqa: BLE001
            st.error(f"อ่านไฟล์ไม่สำเร็จ ตรวจชื่อชีทและคอลัมน์ให้ตรง template — {e}")

    items_df = fetch_items()
    plants_df = fetch_plants()
    item_lookup = items_df.set_index("item_id")["description"].to_dict() if not items_df.empty else {}
    plant_lookup = plants_df.set_index("plant_code")["plant_name"].to_dict() if not plants_df.empty else {}

    st.subheader("Demand Allocations")
    demand_df = fetch_demand(week_id)

    if demand_df.empty:
        st.info("ยังไม่มี Demand สำหรับสัปดาห์นี้ — นำเข้าไฟล์ก่อนด้านบน")
    else:
        rows = []
        for _, row in demand_df.iterrows():
            ratio = list(row["day_ratio"]) if row["day_ratio"] else [20, 20, 20, 20, 20, 0]
            rows.append({
                "demand_id": row["demand_id"],
                "Origin": row["origin_plant"],
                "Origin Name": plant_lookup.get(row["origin_plant"], "—"),
                "Destination": row["destination"],
                "Destination Name": plant_lookup.get(row["destination"], "—"),
                "Product Code": row["item_id"],
                "Description": item_lookup.get(row["item_id"], "—"),
                **{day_headers[i]: int(ratio[i]) for i in range(6)},
            })
        editor_df = pd.DataFrame(rows).set_index("demand_id")

        edited_demand = st.data_editor(
            editor_df,
            hide_index=True,
            use_container_width=True,
            disabled=["Origin", "Origin Name", "Destination", "Destination Name", "Product Code", "Description"],
            column_config={
                h: st.column_config.NumberColumn(h, min_value=0, max_value=100) for h in day_headers
            },
            key=f"demand_editor_{week_id}",
        )

        totals = edited_demand[day_headers].sum(axis=1)
        bad_rows = totals[totals != 100]
        if len(bad_rows):
            st.error(f"มี {len(bad_rows)} รายการสัดส่วนรวมไม่ครบ 100% — แก้ก่อนกด Proceed")

        if st.button("Proceed", type="primary"):
            if len(bad_rows):
                st.error("มีรายการสัดส่วนรวมไม่ครบ 100% — แก้ก่อนคำนวณ")
            else:
                skipped, created = 0, 0
                for did in edited_demand.index:
                    ratio = [int(edited_demand.loc[did, h]) for h in day_headers]
                    update_demand_ratio(did, ratio)
                    if demand_has_master_plan(did):
                        skipped += 1
                        continue
                    row = demand_df[demand_df["demand_id"] == did].iloc[0]
                    it = items_df[items_df["item_id"] == row["item_id"]].iloc[0]
                    alloc = allocate_cases(float(row["weekly_qty"]), [p / 100 for p in ratio])
                    trip = [cases_to_trips(c, float(it["cap_per_truck"])) for c in alloc]
                    insert_master_plan(did, alloc, trip)
                    created += 1
                clear_caches()
                st.success(f"คำนวณเสร็จ: สร้างใหม่ {created} รายการ, ข้าม {skipped} รายการ (มี Master Plan อยู่แล้ว)")
                st.rerun()

    demand_ids = demand_df["demand_id"].tolist() if not demand_df.empty else []
    plan_df = fetch_master_plan_rows(demand_ids)
    if not plan_df.empty:
        st.divider()
        st.subheader("Master Plan")
        d_idx = demand_df.set_index("demand_id")
        pivot = plan_df.pivot(index="demand_id", columns="day_of_week", values="planned_case")
        pivot.columns = [day_headers[c] for c in pivot.columns]
        pivot["รวม"] = pivot.sum(axis=1)
        pivot.index = [
            f"{item_lookup.get(d_idx.loc[i, 'item_id'], i)} · "
            f"{plant_lookup.get(d_idx.loc[i, 'origin_plant'], d_idx.loc[i, 'origin_plant'])} → "
            f"{plant_lookup.get(d_idx.loc[i, 'destination'], d_idx.loc[i, 'destination'])}"
            for i in pivot.index
        ]
        st.dataframe(pivot, use_container_width=True)

        export_rows = []
        for _, r in plan_df.iterrows():
            case_qty = r["planned_case"]
            if case_qty <= 0:
                continue
            d_row = d_idx.loc[r["demand_id"]]
            del_date = week_dates[int(r["day_of_week"])]
            del_date_str = f"{del_date.day:02d}.{del_date.month}.{del_date.year}"
            export_rows.append([
                d_row["origin_plant"],
                d_row["destination"],
                d_row["item_id"],
                case_qty,
                "",
                del_date_str,
            ])
        export_df = pd.DataFrame(
            export_rows, columns=["Plant", "Plant", "Material", "Quantity", "Unit", "Del.date"]
        )
        st.download_button(
            "Export transaction (CSV)",
            export_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"master_plan_{week_id}.csv",
            mime="text/csv",
        )

    with st.expander("⚠️ Reset data"):
        st.caption("ลบข้อมูลทั้งหมดในเว็บแอป ทุกสัปดาห์ (Demand, Master Plan, Revision, Item Master, Plant Master, Stock) — ใช้เมื่อต้องการเริ่มใหม่ทั้งระบบเท่านั้น")
        if st.button("Reset data", type="secondary"):
            reset_all()
            clear_caches()
            for k in list(st.session_state.keys()):
                if k.startswith(("demand_editor_", "editor_", "reason_", "ratio_")) or k in (
                    "dash_week", "mon_week", "tx_week",
                ):
                    del st.session_state[k]
            st.success("ลบข้อมูลทั้งหมดแล้ว")
            st.rerun()

# ---------------------------------------------------------------
# TAB 2: Dashboard
# ---------------------------------------------------------------
with tab2:
    st.subheader("Dashboard")
    weeks = fetch_week_ids()
    if not weeks:
        st.info("ยังไม่มีข้อมูล — เริ่มที่แท็บ 1 ก่อน")
    else:
        dash_week = st.selectbox("สัปดาห์", weeks, key="dash_week")
        d_df = fetch_demand(dash_week)
        i_df = fetch_items()
        p_df = fetch_plants()
        plant_lookup = p_df.set_index("plant_code")["plant_name"].to_dict() if not p_df.empty else {}
        d_df = d_df.copy()
        d_df["destination_name"] = d_df["destination"].map(lambda c: plant_lookup.get(c, c))
        demand_ids = d_df["demand_id"].tolist()
        cur_df = fetch_current_plan_rows(demand_ids)

        if cur_df.empty:
            st.info("สัปดาห์นี้ยังไม่ได้คำนวณ Master Plan")
        else:
            merged = cur_df.merge(d_df[["demand_id", "destination_name"]], on="demand_id")
            total_case = merged["current_case"].sum()
            total_trip = merged["current_trip"].sum()
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("รวมเคสทั้งสัปดาห์", f"{total_case:,.0f}")
            c2.metric("รวมเที่ยวทั้งสัปดาห์", f"{total_trip:,.0f}")
            c3.metric("ปลายทาง", merged["destination_name"].nunique())
            c4.metric("รายการ (item+ปลายทาง)", len(d_df))

            st.markdown("**สรุปเคส แยกปลายทาง/วัน**")
            pivot = merged.pivot_table(
                index="destination_name", columns="day_of_week", values="current_case", aggfunc="sum", fill_value=0
            )
            pivot.columns = [DAY_LABELS[c] for c in pivot.columns]
            pivot["รวม"] = pivot.sum(axis=1)
            st.dataframe(pivot, use_container_width=True)

            by_day = merged.groupby("day_of_week")["current_case"].sum().reindex(range(6), fill_value=0)
            by_day.index = DAY_LABELS
            st.bar_chart(by_day)

        st.divider()
        st.subheader("Upload Performance data (STO)")
        st.caption("นำเข้าข้อมูลส่งจริงรายวัน — คอลัมน์: item_id, destination, delivery_date, actual_case, actual_trip, sto_ref")
        sto_file = st.file_uploader("เลือกไฟล์ STO (.xlsx)", type=["xlsx"], key="sto_upload")
        if sto_file is not None:
            sto_df = pd.read_excel(sto_file, dtype={"item_id": str, "destination": str})
            sto_df = sto_df.merge(
                d_df[["demand_id", "item_id", "destination"]], on=["item_id", "destination"], how="left"
            )
            missing = sto_df["demand_id"].isna().sum()
            sto_df = sto_df.dropna(subset=["demand_id"])
            sto_df["delivery_date"] = pd.to_datetime(sto_df["delivery_date"]).dt.date.astype(str)
            if st.button("นำเข้า STO"):
                insert_actual(sto_df)
                clear_caches()
                if missing:
                    st.warning(f"ข้าม {missing} แถวที่หา item+destination ไม่เจอใน Demand สัปดาห์นี้")
                st.success(f"นำเข้า STO แล้ว {len(sto_df)} แถว")
                st.rerun()
        st.caption("แก้ไขแผนได้ที่แท็บ Edit Plan")

# ---------------------------------------------------------------
# TAB 3: Monitor / Edit
# ---------------------------------------------------------------
with tab3:
    st.subheader("SHOW array เดิม — อ้างอิง Master, Edit ได้")
    weeks = fetch_week_ids()
    if not weeks:
        st.info("ยังไม่มีข้อมูล — เริ่มที่แท็บ 1 ก่อน")
    else:
        mon_week = st.selectbox("สัปดาห์", weeks, key="mon_week")
        d_df = fetch_demand(mon_week)
        i_df = fetch_items()
        p_df = fetch_plants()
        demand_ids = d_df["demand_id"].tolist()
        plan_df = fetch_master_plan_rows(demand_ids)
        cur_df = fetch_current_plan_rows(demand_ids)

        if plan_df.empty:
            st.info("สัปดาห์นี้ยังไม่ได้คำนวณ Master Plan")
        else:
            item_lookup = i_df.set_index("item_id")["description"].to_dict() if not i_df.empty else {}
            plant_lookup = p_df.set_index("plant_code")["plant_name"].to_dict() if not p_df.empty else {}
            d_lookup = d_df.set_index("demand_id")

            label_map = {
                did: (
                    f"{item_lookup.get(d_lookup.loc[did, 'item_id'], did)} — "
                    f"{plant_lookup.get(d_lookup.loc[did, 'origin_plant'], d_lookup.loc[did, 'origin_plant'])} → "
                    f"{plant_lookup.get(d_lookup.loc[did, 'destination'], d_lookup.loc[did, 'destination'])}"
                )
                for did in demand_ids
            }
            sel_label = st.selectbox("รายการ (สินค้า + ปลายทาง)", list(label_map.values()))
            sel_did = [k for k, v in label_map.items() if v == sel_label][0]

            cur_row = cur_df[cur_df["demand_id"] == sel_did].set_index("day_of_week")
            cur_case = [int(cur_row.loc[d, "current_case"]) if d in cur_row.index else 0 for d in range(6)]
            plan_row = plan_df[plan_df["demand_id"] == sel_did].set_index("day_of_week")
            master_case = [int(plan_row.loc[d, "planned_case"]) if d in plan_row.index else 0 for d in range(6)]

            table_df = pd.DataFrame({"วัน": DAY_LABELS, "เคสปัจจุบัน": cur_case})
            edited = st.data_editor(
                table_df, hide_index=True, use_container_width=True,
                disabled=["วัน"], key=f"editor_{sel_did}_{mon_week}",
            )

            diffs = [i for i in range(6) if int(edited.loc[i, "เคสปัจจุบัน"]) != cur_case[i]]
            total_now = sum(cur_case)
            total_master = sum(master_case)
            st.caption(
                f"รวมสัปดาห์นี้: {total_now:,} เคส "
                + ("(ตรงกับ Master Plan)" if total_now == total_master else f"⚠️ ไม่ตรง Master Plan ({total_master:,} เคส)")
            )

            if len(diffs) == 1:
                edit_day = diffs[0]
                new_val = int(edited.loc[edit_day, "เคสปัจจุบัน"])
                diff = new_val - cur_case[edit_day]
                st.markdown(
                    f"**แก้วัน{DAY_LABELS[edit_day]}: {cur_case[edit_day]:,} → {new_val:,} เคส "
                    f"({'+' if diff>0 else ''}{diff:,})**"
                )
                other_days = [d for d in range(6) if d != edit_day]
                comp_options = ["ไม่ต้อง (ยอดรวมจะเปลี่ยนจาก Demand)"] + [
                    f"{DAY_LABELS[d]}: {cur_case[d]:,} → {cur_case[d]-diff:,} เคส" for d in other_days
                ]
                comp_choice = st.selectbox("หักชดเชยจากวัน (กันยอดรวมเพี้ยน)", comp_options)
                reason = st.text_input("เหตุผลที่แก้ไข", key=f"reason_{sel_did}")

                if st.button("Save data", type="primary"):
                    if not reason:
                        st.error("กรุณาระบุเหตุผล")
                    else:
                        it_cap = float(i_df[i_df["item_id"] == d_lookup.loc[sel_did, "item_id"]].iloc[0]["cap_per_truck"])
                        plan_id = plan_row.loc[edit_day, "plan_id"]
                        insert_revision(plan_id, edit_day, new_val, cases_to_trips(new_val, it_cap), reason)

                        if comp_choice != comp_options[0]:
                            comp_day = other_days[comp_options[1:].index(comp_choice)]
                            comp_new = cur_case[comp_day] - diff
                            if comp_new < 0:
                                st.error(f"วัน{DAY_LABELS[comp_day]}จะติดลบ — เลือกวันอื่นหรือแก้จำนวนใหม่")
                                st.stop()
                            comp_plan_id = plan_row.loc[comp_day, "plan_id"]
                            insert_revision(comp_plan_id, comp_day, comp_new, cases_to_trips(comp_new, it_cap), reason)

                        clear_caches()
                        st.success("บันทึกแล้ว")
                        st.rerun()
            elif len(diffs) > 1:
                st.warning("แก้ทีละวันเท่านั้น — กด Save data ให้เรียบร้อยก่อนแก้วันถัดไป (รีเฟรชตารางถ้าค้าง)")

# ---------------------------------------------------------------
# TAB 4: Transactions
# ---------------------------------------------------------------
with tab4:
    st.subheader("Show transactions")
    weeks = fetch_week_ids()
    if not weeks:
        st.info("ยังไม่มีข้อมูล")
    else:
        tx_week = st.selectbox("สัปดาห์", weeks, key="tx_week")
        d_df = fetch_demand(tx_week)
        i_df = fetch_items()
        p_df = fetch_plants()
        demand_ids = d_df["demand_id"].tolist()
        tx_df = fetch_transactions(demand_ids)

        if tx_df.empty:
            st.info("ยังไม่มีการปรับแผนในสัปดาห์นี้")
        else:
            item_lookup = i_df.set_index("item_id")["description"].to_dict() if not i_df.empty else {}
            plant_lookup = p_df.set_index("plant_code")["plant_name"].to_dict() if not p_df.empty else {}
            d_lookup = d_df.set_index("demand_id")
            tx_df["demand_id"] = tx_df["master_plan"].apply(lambda x: x["demand_id"])
            tx_df["สินค้า"] = tx_df["demand_id"].apply(
                lambda did: (
                    f"{item_lookup.get(d_lookup.loc[did, 'item_id'], did)} — "
                    f"{plant_lookup.get(d_lookup.loc[did, 'origin_plant'], d_lookup.loc[did, 'origin_plant'])} → "
                    f"{plant_lookup.get(d_lookup.loc[did, 'destination'], d_lookup.loc[did, 'destination'])}"
                )
            )
            tx_df["วัน"] = tx_df["day_of_week"].apply(lambda d: DAY_LABELS[d])
            show = tx_df[["สินค้า", "วัน", "revised_case", "revised_trip", "reason", "revised_at"]]
            show = show.sort_values("revised_at", ascending=False)
            st.dataframe(show, use_container_width=True, hide_index=True)
