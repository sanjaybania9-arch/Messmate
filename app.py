import csv
import json
import sqlite3
import uuid
import os
import html
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO

import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # only needed when running with a cloud database
    psycopg2 = None

st.set_page_config(page_title="IITR MessMate", page_icon="🍽️", layout="wide", initial_sidebar_state="collapsed")


def secret(name, default=None):
    """Read a value from Streamlit secrets (cloud) or an environment variable (local)."""
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name, default)


DB_FILE = "messmate.db"                         # used only when no DATABASE_URL is set (local testing)
DATABASE_URL = secret("DATABASE_URL")           # permanent cloud database (Neon / Supabase Postgres)
USE_PG = bool(DATABASE_URL)
QR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "c:\\Users\\hp\\Downloads\\my_qr.png")  # keep my_qr.png beside app.py
ADMIN_PASSWORD = secret("ADMIN_PASSWORD", "Admin923")  # set ADMIN_PASSWORD in the app secrets
IST = timezone(timedelta(hours=5, minutes=30), "IST")  # cloud servers run on UTC, so dates are fixed to India time

if USE_PG and psycopg2 is None:
    st.error("The cloud database driver is missing. Add psycopg2-binary to requirements.txt.")
    st.stop()


# Demo student records. Replace with an authorized student directory before deployment.
STUDENTS = [
    {"id": "CH101", "name": "Aarav Sharma", "hostel": "Ganga Bhawan"},
    {"id": "CH102", "name": "Priya Das", "hostel": "Sarojini Bhawan"},
    {"id": "CH103", "name": "Rahul Verma", "hostel": "Cautley Bhawan"},
    {"id": "CH104", "name": "Ananya Singh", "hostel": "Himalaya Bhawan"},
    {"id": "CH105", "name": "Rohan Das", "hostel": "Ganga Bhawan"},
]

# Names are based on IIT Roorkee's official Bhawans/hostels listing.
BHAWANS = [
    "Azad Bhawan", "Cautley Bhawan", "Ganga Bhawan", "Govind Bhawan",
    "Jawahar Bhawan", "Rajendra Bhawan", "Radhakrishnan Bhawan",
    "Rajiv Bhawan", "Ravindra Bhawan", "Malviya Bhawan",
    "Vivekanand Bhawan", "Himalaya Bhawan", "Indira Bhawan",
    "Kasturba Bhawan", "Sarojini Bhawan", "G.P. Hostel",
    "M.R. Chopra Hostel", "Azad Wing", "D.S. Barrack",
    "A.N. Khosla House", "K.I.H.", "Vigyan Bhawan",
]

# Starter menu used for every Bhawan for now. Customize per Bhawan when real menus are available.
MENU = [
    {"name": "Paneer Bhaji", "description": "Paneer cooked with onion and capsicum", "price": 40},
    {"name": "Dahi", "description": "Fresh curd", "price": 20},
    {"name": "Fried Rice", "description": "Rice stir-fried with vegetables", "price": 25},
    {"name": "Egg", "description": "Boiled egg", "price": 10},   # price is a placeholder, change it
    {"name": "Omelet", "description": "Fluffy egg omelet", "price": 20},   # price is a placeholder, change it
]
MEALS = ["Breakfast", "Lunch", "Dinner"]


def run(sql, params=(), fetch=None):
    """Run one SQL statement on Postgres (cloud) or SQLite (local) and commit it."""
    if USE_PG:
        con = psycopg2.connect(DATABASE_URL, connect_timeout=15)
        try:
            with con:
                with con.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql.replace("?", "%s"), tuple(params))
                    if fetch == "all":
                        return [dict(r) for r in cur.fetchall()]
                    if fetch == "one":
                        r = cur.fetchone()
                        return dict(r) if r else None
        finally:
            con.close()
        return None
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    try:
        with con:
            cur = con.execute(sql, tuple(params))
            if fetch == "all":
                return [dict(r) for r in cur.fetchall()]
            if fetch == "one":
                r = cur.fetchone()
                return dict(r) if r else None
    finally:
        con.close()
    return None


def now_ist():
    return datetime.now(IST)


def display_time():
    return now_ist().strftime("%d-%m-%Y %I:%M:%S %p %Z")


def sort_time():
    return now_ist().strftime("%Y-%m-%d %H:%M:%S")


def _sort_time_from_display(text):
    try:
        return datetime.strptime(text.rsplit(" ", 1)[0], "%d-%m-%Y %I:%M:%S %p").strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


@st.cache_resource
def initialise_db():
    run("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            student_name TEXT NOT NULL,
            bhawan TEXT NOT NULL DEFAULT '',
            items_json TEXT NOT NULL,
            total_rupees INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            created_ts TEXT,
            payment_link_id TEXT,
            payment_url TEXT,
            payment_status TEXT NOT NULL DEFAULT 'Pending',
            payment_id TEXT,
            payment_reference TEXT
        )
    """)
    run("""
        CREATE TABLE IF NOT EXISTS meal_reports (
            report_id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            student_name TEXT NOT NULL,
            bhawan TEXT NOT NULL,
            meal TEXT NOT NULL,
            meal_date TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            created_ts TEXT,
            status TEXT NOT NULL DEFAULT 'Submitted'
        )
    """)
    if not USE_PG:
        # Upgrade an older local messmate.db that lacks the newer columns.
        wanted = {
            "orders": {
                "payment_reference": "ALTER TABLE orders ADD COLUMN payment_reference TEXT",
                "bhawan": "ALTER TABLE orders ADD COLUMN bhawan TEXT NOT NULL DEFAULT ''",
                "created_ts": "ALTER TABLE orders ADD COLUMN created_ts TEXT",
            },
            "meal_reports": {
                "created_ts": "ALTER TABLE meal_reports ADD COLUMN created_ts TEXT",
            },
        }
        con = sqlite3.connect(DB_FILE)
        try:
            with con:
                for table, columns in wanted.items():
                    existing = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
                    for column, ddl in columns.items():
                        if column not in existing:
                            con.execute(ddl)
        finally:
            con.close()
    # Give older rows a sortable timestamp so date searches include them.
    for table, key in (("orders", "order_id"), ("meal_reports", "report_id")):
        rows = run(f"SELECT {key}, created_at FROM {table} WHERE created_ts IS NULL OR created_ts = ''", fetch="all")
        for r in rows:
            ts = _sort_time_from_display(r["created_at"])
            if ts:
                run(f"UPDATE {table} SET created_ts = ? WHERE {key} = ?", (ts, r[key]))
    return True


def save_order(order):
    run("""
        INSERT INTO orders (
            order_id, student_id, student_name, bhawan, items_json,
            total_rupees, created_at, created_ts, payment_link_id, payment_url,
            payment_status, payment_id, payment_reference
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order["order_id"], order["student_id"], order["student_name"],
        order["bhawan"], json.dumps(order["items"]), order["total"],
        order["created_at"], sort_time(), order.get("payment_link_id"), order.get("payment_url"),
        order["payment_status"], order.get("payment_id"), order.get("payment_reference")
    ))


def get_order(order_id):
    row = run("SELECT * FROM orders WHERE order_id = ?", (order_id,), fetch="one")
    if row is None:
        return None
    order = dict(row)
    order["items"] = json.loads(order.pop("items_json"))
    order["total"] = order.pop("total_rupees")
    return order


def update_order(order_id, **fields):
    allowed = {"payment_link_id", "payment_url", "payment_status", "payment_id", "payment_reference"}
    if not fields or not set(fields).issubset(allowed):
        raise ValueError("Invalid order update.")
    assignments = ", ".join(f"{field} = ?" for field in fields)
    run(f"UPDATE orders SET {assignments} WHERE order_id = ?", list(fields.values()) + [order_id])


def list_orders(start=None, end=None, student_id=None, bhawan=None, status=None):
    """Orders, newest first. start/end are dates (inclusive) of the day the order was placed."""
    sql, params = "SELECT * FROM orders WHERE 1 = 1", []
    if start:
        sql += " AND substr(created_ts, 1, 10) >= ?"
        params.append(start.isoformat())
    if end:
        sql += " AND substr(created_ts, 1, 10) <= ?"
        params.append(end.isoformat())
    if student_id:
        sql += " AND student_id = ?"
        params.append(student_id)
    if bhawan:
        sql += " AND bhawan = ?"
        params.append(bhawan)
    if status:
        sql += " AND payment_status = ?"
        params.append(status)
    rows = run(sql + " ORDER BY created_ts DESC", params, fetch="all")
    for row in rows:
        row["items"] = json.loads(row["items_json"])
    return rows


def save_meal_report(report):
    run("""
        INSERT INTO meal_reports
        (report_id, student_id, student_name, bhawan, meal, meal_date, reason, created_at, created_ts, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        report["report_id"], report["student_id"], report["student_name"],
        report["bhawan"], report["meal"], report["meal_date"], report["reason"],
        report["created_at"], sort_time(), report["status"]
    ))


def list_meal_reports(start=None, end=None, student_id=None, bhawan=None):
    """Meal reports, newest meal date first. start/end are dates (inclusive) of the meal itself."""
    sql, params = "SELECT * FROM meal_reports WHERE 1 = 1", []
    if start:
        sql += " AND meal_date >= ?"
        params.append(start.isoformat())
    if end:
        sql += " AND meal_date <= ?"
        params.append(end.isoformat())
    if student_id:
        sql += " AND student_id = ?"
        params.append(student_id)
    if bhawan:
        sql += " AND bhawan = ?"
        params.append(bhawan)
    return run(sql + " ORDER BY meal_date DESC, created_ts DESC", params, fetch="all")


def to_csv(rows):
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")  # utf-8-sig so Excel shows the ₹ sign correctly


def items_text(items):
    return ", ".join(f'{x["name"]} × {x["quantity"]}' for x in items)


def make_bill(order):
    buffer = BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm)
    styles = getSampleStyleSheet()
    content = [
        Paragraph("IITR MessMate", styles["Title"]),
        Paragraph("Food Order Bill", styles["Heading2"]), Spacer(1, 10),
        Paragraph(f'<b>Order ID:</b> {order["order_id"]}', styles["Normal"]),
        Paragraph(f'<b>Student ID:</b> {order["student_id"]}', styles["Normal"]),
        Paragraph(f'<b>Bhawan:</b> {order.get("bhawan") or "Not specified"}', styles["Normal"]),
        Paragraph(f'<b>Date and time:</b> {order["created_at"]}', styles["Normal"]), Spacer(1, 12),
    ]
    rows = [["Item", "Qty", "Unit price", "Amount"]]
    for item in order["items"]:
        rows.append([item["name"], str(item["quantity"]), f'Rs. {item["price"]}',
                     f'Rs. {item["price"] * item["quantity"]}'])
    rows.append(["", "", "TOTAL", f'Rs. {order["total"]}'])
    table = Table(rows, colWidths=[75 * mm, 20 * mm, 35 * mm, 35 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("PADDING", (0, 0), (-1, -1), 7),
    ]))
    content.extend([table, Spacer(1, 14),
                    Paragraph(f'<b>Payment status:</b> {order["payment_status"]}', styles["Normal"])])
    if order["payment_status"] == "Paid":
        content.append(Paragraph(
            f'<b>Payment reference:</b> {order.get("payment_id") or order.get("payment_reference") or "Approved by administrator"}',
            styles["Normal"]))
        content.append(Paragraph("Payment reference checked and approved by the administrator.", styles["Normal"]))
    else:
        content.append(Paragraph("This is an order bill, not a receipt for completed payment.", styles["Normal"]))
    pdf.build(content)
    return buffer.getvalue()



try:
    initialise_db()
except Exception as error:
    st.error("Could not connect to the database. Check DATABASE_URL in the app secrets.")
    st.caption(str(error))
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
#  INTERFACE
#  Idea: a hostel mess coupon. Deep Ganga-canal teal, steel mist, turmeric and
#  chilli red. Menu items are tear-off coupons, the hero carries a "now serving"
#  coupon, and navigation is a floating dock at the bottom instead of a sidebar.
# ─────────────────────────────────────────────────────────────────────────────
THEME_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Albert+Sans:wght@400;500;600&family=Rozha+One&display=swap');

:root {
    --mist: #E7EDEA;
    --paper: #FFFFFF;
    --ink: #10282B;
    --canal: #0B3A3F;
    --canal-soft: #145159;
    --turmeric: #F2A900;
    --turmeric-deep: #D99500;
    --chilli: #B8322A;
    --steel: #5E767A;
    --line: #CBD6D2;
}

/* Page frame ------------------------------------------------------------- */
html, body, .stApp { background: var(--mist) !important; }
.stApp, .stApp p, .stApp label, .stApp li, .stApp button, .stApp input, .stApp textarea {
    font-family: 'Albert Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
    color: var(--ink);
}
.block-container { max-width: 1040px; padding: 1.6rem 1.5rem 8rem; }

/* Remove the sidebar and default Streamlit chrome ------------------------ */
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"], [data-testid="stToolbar"],
[data-testid="stDecoration"], footer, #MainMenu { display: none !important; }
[data-testid="stHeader"] { background: transparent; height: 0; }

/* Hero ------------------------------------------------------------------- */
.hero {
    position: relative;
    display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
    gap: 2rem;
    padding: 3rem 3rem 3rem 3.2rem;
    border-radius: 28px;
    color: #F1F6F4;
    background:
        repeating-radial-gradient(circle at 86% 50%, transparent 0 26px, rgba(255,255,255,.055) 26px 28px),
        var(--canal);
    overflow: hidden;
}
.hero-copy { flex: 1 1 340px; }
.wordmark {
    font-family: 'Rozha One', Georgia, serif;
    font-size: clamp(3.2rem, 9vw, 6rem);
    line-height: .95;
    letter-spacing: -.01em;
    color: #FFFFFF;
}
.hero-lead { margin: 1.1rem 0 0; max-width: 34ch; font-size: 1.12rem; line-height: 1.55; color: #C9DBD7 !important; }

.coupon {
    position: relative;
    display: flex; flex-direction: column; align-items: center; gap: .15rem;
    padding: 1.5rem 2.6rem;
    background: var(--turmeric);
    border-radius: 14px;
    transform: rotate(-2deg);
    outline: 2px dashed rgba(11,58,63,.4);
    outline-offset: -9px;
    animation: settle .7s cubic-bezier(.2,.8,.2,1) both;
}
.coupon::before, .coupon::after {
    content: ""; position: absolute; top: 50%; width: 22px; height: 22px;
    margin-top: -11px; border-radius: 50%; background: var(--canal);
}
.coupon::before { left: -11px; }
.coupon::after { right: -11px; }
.coupon-label { font-size: .9rem; font-weight: 500; color: var(--canal); }
.coupon-meal { font-family: 'Rozha One', Georgia, serif; font-size: 2.5rem; line-height: 1.1; color: var(--canal); }
.coupon-date { font-size: .95rem; font-weight: 500; color: var(--canal); }
@keyframes settle {
    from { opacity: 0; transform: translateX(34px) rotate(5deg); }
    to   { opacity: 1; transform: translateX(0) rotate(-2deg); }
}
@media (prefers-reduced-motion: reduce) { .coupon { animation: none; } }
@media (max-width: 640px) { .hero { padding: 2rem 1.5rem; } .coupon { padding: 1.2rem 2rem; } }

/* Section headings -------------------------------------------------------- */
.hd { margin: 3rem 0 1.1rem; }
.hd-title { font-family: 'Rozha One', Georgia, serif; font-size: 2.1rem; line-height: 1.15; color: var(--canal); }
.hd-note { margin: .4rem 0 0; max-width: 64ch; color: var(--steel) !important; line-height: 1.55; }

/* Bhawan and ID card ------------------------------------------------------ */
.st-key-pass {
    background: var(--paper); border: 1px solid var(--line);
    border-radius: 20px; padding: 1.3rem 1.5rem .6rem;
}

/* Menu coupons ------------------------------------------------------------ */
[data-testid="stForm"] { border: none; padding: 0; background: transparent; }
[class*="st-key-ticket_"] {
    position: relative;
    background: var(--paper); border: 1px solid var(--line);
    border-radius: 18px; padding: 1rem 1.3rem; margin-bottom: .7rem;
}
[class*="st-key-ticket_"] [data-testid="stHorizontalBlock"] { align-items: center; }
[class*="st-key-ticket_"] [data-testid="stColumn"]:nth-child(2) {
    position: relative; border-left: 2px dashed var(--line); padding-left: 1.4rem;
}
[class*="st-key-ticket_"] [data-testid="stColumn"]:nth-child(2)::before,
[class*="st-key-ticket_"] [data-testid="stColumn"]:nth-child(2)::after {
    content: ""; position: absolute; left: -11px; width: 20px; height: 20px;
    border-radius: 50%; background: var(--mist);
}
[class*="st-key-ticket_"] [data-testid="stColumn"]:nth-child(2)::before { top: calc(-1rem - 10px); }
[class*="st-key-ticket_"] [data-testid="stColumn"]:nth-child(2)::after { bottom: calc(-1rem - 10px); }
.t-name { font-family: 'Rozha One', Georgia, serif; font-size: 1.55rem; line-height: 1.2; color: var(--canal); }
.t-desc { margin-top: .2rem; color: var(--steel); }
.t-price { font-family: 'Rozha One', Georgia, serif; font-size: 1.9rem; line-height: 1; color: var(--chilli); }
.t-price small { font-family: 'Albert Sans', sans-serif; font-size: .85rem; margin-left: .35rem; color: var(--steel); }

/* Order facts ------------------------------------------------------------- */
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .8rem 1.5rem; margin-bottom: 1rem; }
.fact span { display: block; font-size: .85rem; color: var(--steel); }
.fact b { display: block; margin-top: .1rem; font-weight: 600; font-size: 1.02rem; word-break: break-word; }

/* Inputs ------------------------------------------------------------------ */
[data-testid="stWidgetLabel"] p { font-size: .88rem; font-weight: 600; color: var(--steel); }
.stApp div[data-baseweb="input"], .stApp div[data-baseweb="textarea"], .stApp div[data-baseweb="select"] > div {
    background: #FFFFFF; border: 1px solid var(--line); border-radius: 12px;
}
.stApp div[data-baseweb="input"]:focus-within, .stApp div[data-baseweb="textarea"]:focus-within,
.stApp div[data-baseweb="select"] > div:focus-within { border-color: var(--canal); box-shadow: 0 0 0 1px var(--canal); }
.stApp div[data-baseweb="base-input"] { background: #FFFFFF !important; }
.stApp input, .stApp textarea,
.stApp div[data-baseweb="input"] input, .stApp div[data-baseweb="base-input"] input,
.stApp div[data-baseweb="textarea"] textarea,
.stApp div[data-baseweb="select"] input, .stApp div[data-baseweb="select"] div {
    background-color: #FFFFFF !important;
    color: #10282B !important;
    -webkit-text-fill-color: #10282B !important;
    caret-color: #0B3A3F !important;
}
.stApp input::placeholder, .stApp textarea::placeholder {
    color: #8A9EA2 !important; -webkit-text-fill-color: #8A9EA2 !important; opacity: 1;
}
.stApp div[data-baseweb="input"] button { background: #FFFFFF !important; color: #10282B !important; }
.stApp div[data-baseweb="input"] button svg { fill: #10282B !important; }
/* Dropdown menus and date picker open in a popup outside the app container */
div[data-baseweb="popover"], div[data-baseweb="popover"] ul, div[data-baseweb="popover"] li,
div[data-baseweb="calendar"], div[data-baseweb="calendar"] div {
    background-color: #FFFFFF !important; color: #10282B !important;
}
div[data-baseweb="popover"] li:hover, div[data-baseweb="popover"] li[aria-selected="true"] {
    background-color: #E7EDEA !important;
}

/* Buttons ----------------------------------------------------------------- */
[data-testid^="stBaseButton"] {
    min-height: 3rem; padding: .6rem 1.5rem; border-radius: 999px; font-weight: 600;
    transition: background .2s ease, color .2s ease, border-color .2s ease;
}
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"],
[data-testid="stBaseButton-secondaryFormSubmit"] {
    background: var(--turmeric) !important; color: var(--canal) !important; border: none !important;
}
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover,
[data-testid="stBaseButton-secondaryFormSubmit"]:hover { background: var(--turmeric-deep) !important; }
[data-testid="stBaseButton-secondary"] {
    background: transparent !important; color: var(--canal) !important; border: 1.5px solid var(--canal) !important;
}
[data-testid="stBaseButton-secondary"]:hover { background: var(--canal) !important; color: #FFFFFF !important; }
.stApp button:focus-visible { outline: 3px solid var(--canal); outline-offset: 2px; }

/* Alerts, tables, metrics, expanders -------------------------------------- */
[data-testid="stAlert"] { border-radius: 14px; }
[data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
[data-testid="stMetricValue"] { font-family: 'Rozha One', Georgia, serif; color: var(--chilli); }
[data-testid="stMetricLabel"] p { color: var(--steel); }
[data-testid="stExpander"], [data-testid="stExpander"] details {
    background: var(--paper); border-color: var(--line); border-radius: 16px;
}
hr { border-color: var(--line) !important; }

/* Floating navigation dock ------------------------------------------------ */
.st-key-dock {
    position: fixed; left: 50%; bottom: 1.5rem; transform: translateX(-50%);
    z-index: 1000; width: auto !important;
    background: var(--canal); padding: .35rem; border-radius: 999px;
    box-shadow: 0 14px 34px rgba(11,58,63,.38);
}
.st-key-dock [data-testid="stRadio"] [role="radiogroup"] { flex-direction: row; gap: .2rem; }
.st-key-dock [data-testid="stRadio"] label {
    margin: 0; padding: .65rem 1.6rem; border-radius: 999px; cursor: pointer;
    color: #CFE0DC; transition: background .2s ease, color .2s ease;
}
.st-key-dock [data-testid="stRadio"] label > div:first-child { display: none; }
.st-key-dock [data-testid="stRadio"] label p { margin: 0; color: inherit !important; font-size: .98rem; font-weight: 500; }
.st-key-dock [data-testid="stRadio"] label:hover { color: #FFFFFF; }
.st-key-dock [data-testid="stRadio"] label:has(input:checked) { background: var(--turmeric); color: var(--canal); }
.st-key-dock [data-testid="stRadio"] label:has(input:checked) p { font-weight: 600; }
.st-key-dock [data-testid="stRadio"] label:has(input:focus-visible) { outline: 3px solid #FFFFFF; outline-offset: 2px; }

/* Admin sub-navigation ---------------------------------------------------- */
.st-key-subnav [data-testid="stRadio"] [role="radiogroup"] { gap: .5rem; }
.st-key-subnav [data-testid="stRadio"] label {
    margin: 0; padding: .55rem 1.3rem; border: 1.5px solid var(--canal);
    border-radius: 999px; cursor: pointer; background: transparent;
    transition: background .2s ease, color .2s ease;
}
.st-key-subnav [data-testid="stRadio"] label > div:first-child { display: none; }
.st-key-subnav [data-testid="stRadio"] label p { margin: 0; color: inherit !important; font-weight: 500; }
.st-key-subnav [data-testid="stRadio"] label:has(input:checked) { background: var(--canal); color: #FFFFFF; }
"""
st.markdown(f"<style>{THEME_CSS}</style>", unsafe_allow_html=True)


def esc(value):
    return html.escape(str(value))


def heading(title, note=""):
    note_html = f'<p class="hd-note">{esc(note)}</p>' if note else ""
    st.markdown(f'<div class="hd"><div class="hd-title">{esc(title)}</div>{note_html}</div>',
                unsafe_allow_html=True)


def facts(pairs):
    cells = "".join(f'<div class="fact"><span>{esc(k)}</span><b>{esc(v)}</b></div>' for k, v in pairs)
    st.markdown(f'<div class="facts">{cells}</div>', unsafe_allow_html=True)


def masthead():
    now = now_ist()
    meal_now = "Breakfast" if now.hour < 10 else "Lunch" if now.hour < 16 else "Dinner"
    st.markdown(
        '<div class="hero">'
        '<div class="hero-copy"><div class="wordmark">MessMate</div>'
        '<p class="hero-lead">Order from your Bhawan mess at IIT Roorkee, pay with UPI, '
        'and tell the mess when you will skip a meal.</p></div>'
        f'<div class="coupon"><span class="coupon-label">Now serving</span>'
        f'<span class="coupon-meal">{meal_now}</span>'
        f'<span class="coupon-date">{now.strftime("%A, %d %B")}</span></div>'
        '</div>',
        unsafe_allow_html=True,
    )


# The dock (rendered at the end of the script) stores its choice under the "page" key.
page = st.session_state.get("page", "Student Portal")
masthead()

if page == "Student Portal":
    heading("Your Bhawan and ID", "Choose your Bhawan to see its mess menu, then enter your Student ID to place an order.")
    with st.container(key="pass"):
        pass_left, pass_right = st.columns(2)
        with pass_left:
            bhawan = st.selectbox("Select your Bhawan / Hostel", BHAWANS)
        with pass_right:
            student_id_input = st.text_input("Student ID", placeholder="e.g. CH101", key="portal_student_id").strip().upper()
    student = next((s for s in STUDENTS if s["id"] == student_id_input), None)

    heading(f"{bhawan} mess menu",
            "Demo menu and prices only. The same starter menu is currently shown for every Bhawan; "
            "update it with each mess's actual menu before launch.")

    with st.form("food_order_form"):
        quantities = {}
        for i, item in enumerate(MENU):
            with st.container(key=f"ticket_{i}"):
                left, right = st.columns([3, 1.3])
                with left:
                    st.markdown(
                        f'<div class="t-name">{esc(item["name"])}</div>'
                        f'<div class="t-desc">{esc(item["description"])}</div>',
                        unsafe_allow_html=True)
                with right:
                    st.markdown(f'<div class="t-price">₹{item["price"]}<small>each</small></div>',
                                unsafe_allow_html=True)
                    quantities[item["name"]] = st.number_input("Quantity", min_value=0, max_value=20,
                        value=0, step=1, key=f"quantity_{i}")
        create_order = st.form_submit_button("Review and pay", width="stretch")

    if create_order:
        chosen_items = [{"name": item["name"], "price": item["price"], "quantity": int(quantities[item["name"]])}
                        for item in MENU if int(quantities[item["name"]]) > 0]
        if student is None:
            st.error("Enter a valid demo Student ID. In this prototype, the accepted IDs are CH101–CH105.")
        elif not chosen_items:
            st.warning("Choose at least one menu item.")
        else:
            total = sum(item["price"] * item["quantity"] for item in chosen_items)
            order = {
                "order_id": "MM-" + uuid.uuid4().hex[:12].upper(),
                "student_id": student["id"], "student_name": student["name"], "bhawan": bhawan,
                "items": chosen_items, "total": total,
                "created_at": display_time(),
                "payment_status": "Pending", "payment_link_id": None, "payment_url": None,
                "payment_id": None, "payment_reference": None,
            }
            save_order(order)
            st.session_state["active_order_id"] = order["order_id"]
            st.success("Order created. Review the payment details below.")

    active_id = st.session_state.get("active_order_id")
    if active_id:
        order = get_order(active_id)
        if order:
            heading("Pay for your order")
            facts([
                ("Order ID", order["order_id"]),
                ("Bhawan", order.get("bhawan") or "Not specified"),
                ("Student ID", order["student_id"]),
            ])
            st.dataframe([{"Item": x["name"], "Quantity": x["quantity"], "Unit price (₹)": x["price"],
                           "Amount (₹)": x["price"] * x["quantity"]} for x in order["items"]],
                         width="stretch", hide_index=True)
            st.metric("Total to pay", f'₹{order["total"]}')
            if order["payment_status"] == "Pending":
                qr_col, ref_col = st.columns([1, 1.2], gap="large")
                with qr_col:
                    if os.path.isfile(QR_FILE):
                        st.image(QR_FILE, width=280)
                        st.caption("Scan with your UPI app and confirm the recipient name and exact amount before paying.")
                    else:
                        st.error(f"QR image not found: {QR_FILE}. Put your QR image beside app.py and update QR_FILE if needed.")
                with ref_col:
                    st.info("After paying, submit the transaction reference. An admin must verify the actual incoming payment; entering a reference does not automatically prove payment.")
                    with st.form(f"payment_reference_form_{order['order_id']}"):
                        payment_reference = st.text_input("UPI transaction reference / UTR", value=order.get("payment_reference") or "")
                        reference_submit = st.form_submit_button("Submit payment reference", width="stretch")
                    if reference_submit:
                        clean_reference = payment_reference.strip()
                        if len(clean_reference) < 6:
                            st.error("Enter the transaction reference shown by your UPI app.")
                        else:
                            update_order(order["order_id"], payment_reference=clean_reference, payment_status="Submitted")
                            st.success("Reference submitted for manual verification.")
                            st.rerun()
            elif order["payment_status"] == "Submitted":
                st.warning("Payment reference submitted. Waiting for admin verification.")
            elif order["payment_status"] == "Rejected":
                st.error("The reference was not approved. Contact the mess administrator before paying again.")
            order = get_order(active_id)
            if order["payment_status"] == "Paid":
                st.success("Payment verified by the administrator.")
            else:
                st.warning(f'Payment status: {order["payment_status"]}')
            st.download_button(
                "Download paid receipt (PDF)" if order["payment_status"] == "Paid" else "Download order summary (PDF, not a paid receipt)",
                data=make_bill(order), file_name=f'{order["order_id"]}_bill.pdf', mime="application/pdf", width="stretch"
            )

    heading("Skipping a meal?",
            "Tell your Bhawan mess about a breakfast, lunch, or dinner you cannot take. The report is saved for the "
            "administrator to review and pass to the relevant mess; this prototype does not send an automatic notification yet.")
    with st.form("meal_report_form"):
        report_bhawan = st.selectbox("Bhawan for this report", BHAWANS, index=BHAWANS.index(bhawan), key="report_bhawan")
        report_student_id = st.text_input("Student ID for report", value=student_id_input, key="report_student_id").strip().upper()
        report_meal = st.selectbox("Meal you cannot take", MEALS)
        report_date = st.date_input("Meal date", value=now_ist().date())
        report_reason = st.text_area("Optional note to the mess", placeholder="e.g. I will be away from campus")
        submit_report = st.form_submit_button("Send meal report", width="stretch")
    if submit_report:
        report_student = next((s for s in STUDENTS if s["id"] == report_student_id), None)
        if report_student is None:
            st.error("Enter a valid demo Student ID (CH101–CH105).")
        else:
            save_meal_report({
                "report_id": "MR-" + uuid.uuid4().hex[:10].upper(),
                "student_id": report_student["id"], "student_name": report_student["name"],
                "bhawan": report_bhawan, "meal": report_meal, "meal_date": report_date.isoformat(),
                "reason": report_reason.strip(), "created_at": display_time(),
                "status": "Submitted",
            })
            st.success(f"Your {report_meal.lower()} report has been saved for {report_bhawan}.")

    heading("Your orders and reports", "Look up what you have ordered or reported, for any dates you choose.")
    hist_from_col, hist_to_col = st.columns(2)
    with hist_from_col:
        hist_from = st.date_input("From date", value=now_ist().date() - timedelta(days=30), key="hist_from")
    with hist_to_col:
        hist_to = st.date_input("To date", value=now_ist().date(), key="hist_to")
    if student is None:
        st.info("Enter a valid Student ID at the top of the page to see your history.")
    elif hist_from > hist_to:
        st.error("The From date must be on or before the To date.")
    else:
        my_orders = list_orders(start=hist_from, end=hist_to, student_id=student["id"])
        st.write(f"**Orders:** {len(my_orders)} found")
        if my_orders:
            st.dataframe([{
                "Order ID": o["order_id"], "Placed on": o["created_at"], "Bhawan": o["bhawan"] or "-",
                "Items": items_text(o["items"]), "Total (₹)": o["total_rupees"], "Payment status": o["payment_status"],
            } for o in my_orders], width="stretch", hide_index=True)
            bill_choice = st.selectbox("Download a bill", [o["order_id"] for o in my_orders], key="hist_bill_choice")
            st.download_button("Download bill (PDF)", data=make_bill(get_order(bill_choice)),
                               file_name=f"{bill_choice}_bill.pdf", mime="application/pdf", key="hist_bill_download")
        else:
            st.info("You have not placed any orders in these dates.")
        my_reports = list_meal_reports(start=hist_from, end=hist_to, student_id=student["id"])
        st.write(f"**Meal reports:** {len(my_reports)} found")
        if my_reports:
            st.dataframe([{
                "Report ID": r["report_id"], "Meal date": r["meal_date"], "Meal": r["meal"],
                "Bhawan": r["bhawan"], "Note": r["reason"] or "-", "Status": r["status"],
            } for r in my_reports], width="stretch", hide_index=True)
        else:
            st.info("You have not reported any skipped meals in these dates.")

else:
    heading("Mess office", "Sign in with the administrator password to verify payments and read meal reports.")
    _, login_col, _ = st.columns([1, 1.4, 1])
    with login_col:
        admin_password = st.text_input("Admin password", type="password", key="admin_password")
        if admin_password == ADMIN_PASSWORD:
            st.success("Signed in as administrator.")
        elif admin_password:
            st.error("Incorrect admin password.")

    if admin_password == ADMIN_PASSWORD:
        with st.container(key="subnav"):
            admin_section = st.radio("Admin options", ["Payment Verifications", "Records by date", "Students List Check"],
                                     horizontal=True, label_visibility="collapsed")

        if admin_section == "Payment Verifications":
            orders = list_orders()
            review_orders = [o for o in orders if o.get("payment_status") == "Submitted"]
            heading("Payment verifications",
                    f"{len(review_orders)} order(s) waiting for your check." if review_orders
                    else "Nothing is waiting for verification right now.")
            for row in review_orders:
                order = get_order(row["order_id"])
                with st.expander(f'{order.get("bhawan") or "Bhawan not set"}: ₹{order["total"]} ({order["order_id"]})'):
                    facts([
                        ("Student", f'{order["student_name"]} ({order["student_id"]})'),
                        ("Bhawan", order.get("bhawan") or "Not specified"),
                        ("Order date", order["created_at"]),
                        ("Expected amount", f'₹{order["total"]}'),
                        ("Submitted UPI reference", order.get("payment_reference") or "Not submitted"),
                    ])
                    st.write("**Items:**")
                    for item in order["items"]:
                        st.write(f'- {item["name"]} × {item["quantity"]}: ₹{item["price"] * item["quantity"]}')
                    st.warning("Verify the real incoming transaction and exact amount before approving. Do not rely only on a screenshot.")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Approve payment", key=f'approve_{order["order_id"]}', type="primary", width="stretch"):
                            update_order(order["order_id"], payment_status="Paid", payment_id=order.get("payment_reference"))
                            st.rerun()
                    with c2:
                        if st.button("Reject reference", key=f'reject_{order["order_id"]}', width="stretch"):
                            update_order(order["order_id"], payment_status="Rejected")
                            st.rerun()

        elif admin_section == "Records by date":
            heading("Records by date", "Choose the dates you need. Orders are filtered by the day they were placed, and meal reports by the meal date.")
            today_ist = now_ist().date()
            f1, f2, f3, f4 = st.columns(4)
            with f1:
                rec_from = st.date_input("From date", value=today_ist - timedelta(days=6), key="rec_from")
            with f2:
                rec_to = st.date_input("To date", value=today_ist, key="rec_to")
            with f3:
                rec_bhawan = st.selectbox("Bhawan", ["All Bhawans"] + BHAWANS, key="rec_bhawan")
            with f4:
                rec_status = st.selectbox("Payment status", ["All statuses", "Pending", "Submitted", "Paid", "Rejected"], key="rec_status")
            if rec_from > rec_to:
                st.error("The From date must be on or before the To date.")
            else:
                bhawan_arg = None if rec_bhawan == "All Bhawans" else rec_bhawan
                status_arg = None if rec_status == "All statuses" else rec_status
                rec_orders = list_orders(start=rec_from, end=rec_to, bhawan=bhawan_arg, status=status_arg)
                paid_total = sum(o["total_rupees"] for o in rec_orders if o["payment_status"] == "Paid")
                heading("Orders", f"{len(rec_orders)} order(s) found. ₹{paid_total} verified as paid.")
                if rec_orders:
                    order_rows = [{
                        "Order ID": o["order_id"], "Placed on": o["created_at"], "Student ID": o["student_id"],
                        "Student": o["student_name"], "Bhawan": o["bhawan"] or "-", "Items": items_text(o["items"]),
                        "Total (₹)": o["total_rupees"], "Payment status": o["payment_status"],
                        "UPI reference": o.get("payment_reference") or "-",
                    } for o in rec_orders]
                    st.dataframe(order_rows, width="stretch", hide_index=True)
                    st.download_button("Download orders (CSV)", data=to_csv(order_rows),
                                       file_name=f"orders_{rec_from}_to_{rec_to}.csv", mime="text/csv", key="orders_csv")
                else:
                    st.info("No orders match these filters.")

                rec_reports = list_meal_reports(start=rec_from, end=rec_to, bhawan=bhawan_arg)
                heading("Meal reports", f"{len(rec_reports)} report(s) found for these meal dates.")
                if rec_reports:
                    counts = {}
                    for r in rec_reports:
                        row_counts = counts.setdefault((r["meal_date"], r["bhawan"]), {m: 0 for m in MEALS})
                        row_counts[r["meal"]] = row_counts.get(r["meal"], 0) + 1
                    st.write("**Meals skipped, by date and Bhawan**")
                    st.dataframe([{"Meal date": d, "Bhawan": b, **c} for (d, b), c in sorted(counts.items(), reverse=True)],
                                 width="stretch", hide_index=True)
                    report_rows = [{
                        "Report ID": r["report_id"], "Meal date": r["meal_date"], "Meal": r["meal"],
                        "Student ID": r["student_id"], "Student": r["student_name"], "Bhawan": r["bhawan"],
                        "Note": r["reason"] or "-", "Submitted at": r["created_at"], "Status": r["status"],
                    } for r in rec_reports]
                    st.write("**All reports**")
                    st.dataframe(report_rows, width="stretch", hide_index=True)
                    st.download_button("Download meal reports (CSV)", data=to_csv(report_rows),
                                       file_name=f"meal_reports_{rec_from}_to_{rec_to}.csv", mime="text/csv", key="reports_csv")
                else:
                    st.info("No meal reports match these filters.")

        else:
            heading("Students on record")
            st.dataframe(STUDENTS, width="stretch", hide_index=True)
            heading("Meal reports from students")
            reports = list_meal_reports()
            if not reports:
                st.info("No meal reports have been submitted.")
            else:
                st.dataframe([{
                    "Report ID": r["report_id"], "Student ID": r["student_id"], "Student Name": r["student_name"],
                    "Bhawan": r["bhawan"], "Meal": r["meal"], "Meal Date": r["meal_date"],
                    "Note": r["reason"] or "-", "Submitted At": r["created_at"], "Status": r["status"]
                } for r in reports], width="stretch", hide_index=True)
                st.caption("Meal reports are stored here for the administrator to relay to the appropriate Bhawan mess. No automatic email/SMS notification is configured.")
    st.caption("Prototype only: one shared password is not production-grade authentication. Configure real authentication and per-student identity checks before public deployment.")

# Floating navigation dock (replaces the sidebar).
with st.container(key="dock"):
    st.radio("Navigate", ["Student Portal", "Admin"], key="page", horizontal=True, label_visibility="collapsed")

