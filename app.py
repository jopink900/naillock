import base64
import datetime as dt
import io
import os
import sqlite3
import textwrap
import zipfile
from dataclasses import dataclass
from typing import Optional, Tuple, List

import pandas as pd
import streamlit as st

# ---------------------------
# NailLock™ — v2 (complete)
# ---------------------------

APP_TITLE = "NailLock™"
APP_TAGLINE = "A guided consistency system for fungal nails. Not medical advice."
DB_FILE = "naillock.db"  # best-effort persistence (Streamlit Cloud may reset sometimes)

st.set_page_config(page_title="NailLock", page_icon="🦶", layout="wide")

# ---------- Style / UX polish ----------
st.markdown(
    """
<style>
    .nl-hero {font-size: 46px; font-weight: 800; letter-spacing:-0.02em; margin-bottom: 0.1rem;}
    .nl-sub {opacity: 0.75; margin-top: 0.25rem;}
    .nl-card {border: 1px solid rgba(0,0,0,0.12); border-radius: 12px; padding: 14px 16px; background: rgba(255,255,255,0.02);}
    .nl-pill {display:inline-block; padding: 6px 10px; border-radius:999px; border:1px solid rgba(0,0,0,0.12); margin-right:8px;}
    .nl-small {font-size: 13px; opacity: 0.75;}
    .nl-danger {color: #b00020;}
    .nl-ok {color: #1b5e20;}
    .nl-muted {opacity: 0.7;}
</style>
""",
    unsafe_allow_html=True,
)

# ---------- Core: DB ----------
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_log (
            day TEXT PRIMARY KEY,
            did_treatment INTEGER DEFAULT 0,
            washed_dried INTEGER DEFAULT 0,
            fresh_socks INTEGER DEFAULT 0,
            shoes_aired INTEGER DEFAULT 0,
            notes TEXT DEFAULT ''
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            kind TEXT NOT NULL, -- 'before' | 'after' | 'weekly'
            filename TEXT,
            mime TEXT,
            data_b64 TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()

def set_setting(key: str, value: str):
    conn = db()
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    conn.close()

def get_setting(key: str, default: str = "") -> str:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else default

def upsert_daily(day: str, did_treatment: int, washed_dried: int, fresh_socks: int, shoes_aired: int, notes: str):
    conn = db()
    conn.execute("""
        INSERT INTO daily_log(day, did_treatment, washed_dried, fresh_socks, shoes_aired, notes)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
            did_treatment=excluded.did_treatment,
            washed_dried=excluded.washed_dried,
            fresh_socks=excluded.fresh_socks,
            shoes_aired=excluded.shoes_aired,
            notes=excluded.notes;
    """, (day, did_treatment, washed_dried, fresh_socks, shoes_aired, notes))
    conn.commit()
    conn.close()

def get_daily(day: str) -> dict:
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT day, did_treatment, washed_dried, fresh_socks, shoes_aired, notes FROM daily_log WHERE day=?", (day,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"day": day, "did_treatment": 0, "washed_dried": 0, "fresh_socks": 0, "shoes_aired": 0, "notes": ""}
    return {"day": row[0], "did_treatment": row[1], "washed_dried": row[2], "fresh_socks": row[3], "shoes_aired": row[4], "notes": row[5] or ""}

def get_all_daily() -> pd.DataFrame:
    conn = db()
    df = pd.read_sql_query("SELECT * FROM daily_log ORDER BY day ASC", conn)
    conn.close()
    if df.empty:
        return pd.DataFrame(columns=["day","did_treatment","washed_dried","fresh_socks","shoes_aired","notes"])
    return df

def add_photo(day: str, kind: str, filename: str, mime: str, data_bytes: bytes):
    # store as base64 text (simple, portable). keep uploads reasonable.
    if len(data_bytes) > 3_000_000:  # 3MB cap to avoid insane storage
        raise ValueError("Photo too large (max ~3MB). Resize and try again.")
    conn = db()
    b64 = base64.b64encode(data_bytes).decode("utf-8")
    conn.execute(
        "INSERT INTO photos(day, kind, filename, mime, data_b64) VALUES(?,?,?,?,?)",
        (day, kind, filename, mime, b64),
    )
    conn.commit()
    conn.close()

def list_photos(kind: Optional[str] = None) -> pd.DataFrame:
    conn = db()
    if kind:
        df = pd.read_sql_query("SELECT id, day, kind, filename, mime, data_b64 FROM photos WHERE kind=? ORDER BY day ASC, id ASC", conn, params=(kind,))
    else:
        df = pd.read_sql_query("SELECT id, day, kind, filename, mime, data_b64 FROM photos ORDER BY day ASC, id ASC", conn)
    conn.close()
    return df

def delete_photo(photo_id: int):
    conn = db()
    conn.execute("DELETE FROM photos WHERE id=?", (photo_id,))
    conn.commit()
    conn.close()

init_db()

# ---------- Helpers ----------
def today_iso() -> str:
    return dt.date.today().isoformat()

def parse_iso(d: str) -> dt.date:
    return dt.date.fromisoformat(d)

def score_row(row: dict) -> int:
    return int(row["did_treatment"]) + int(row["washed_dried"]) + int(row["fresh_socks"]) + int(row["shoes_aired"])

def streak_and_done_days(df: pd.DataFrame) -> Tuple[int, set]:
    done_days = set()
    if df is None or df.empty:
        return 0, done_days

    # "done" means at least 1 action logged that day
    for _, r in df.iterrows():
        if int(r.get("did_treatment",0)) or int(r.get("washed_dried",0)) or int(r.get("fresh_socks",0)) or int(r.get("shoes_aired",0)):
            done_days.add(r["day"])

    # streak ending today (or yesterday if today not yet done)
    streak = 0
    cursor = dt.date.today()
    # if today not done, start from yesterday
    if cursor.isoformat() not in done_days:
        cursor = cursor - dt.timedelta(days=1)

    while cursor.isoformat() in done_days:
        streak += 1
        cursor -= dt.timedelta(days=1)

    return streak, done_days

def level_for(streak: int) -> str:
    if streak >= 90: return "Titan"
    if streak >= 60: return "Platinum"
    if streak >= 30: return "Gold"
    if streak >= 14: return "Silver"
    if streak >= 7:  return "Bronze"
    return "Starter"

def badge_for(streak: int) -> str:
    if streak >= 90: return "🏆 90-Day Lock"
    if streak >= 60: return "💠 60-Day Steel"
    if streak >= 30: return "🥇 30-Day Gold"
    if streak >= 14: return "🥈 14-Day Silver"
    if streak >= 7:  return "🥉 7-Day Bronze"
    return "🟢 Start"

def make_daily_ics(title: str, hour: int, minute: int, days: int) -> str:
    start = dt.datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    dtstart = start.strftime("%Y%m%dT%H%M%S")
    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//NailLock//EN
BEGIN:VEVENT
UID:naillock-{dtstart}@naillock
DTSTAMP:{dtstart}
DTSTART:{dtstart}
RRULE:FREQ=DAILY;COUNT={days}
SUMMARY:{title}
DESCRIPTION:Do the actions. Log the streak. Keep it moving.
END:VEVENT
END:VCALENDAR
"""
    return ics

def render_heatmap(done_days: set, days_back: int = 35):
    st.caption("Consistency map (last ~5 weeks). 🟩 done, ⬛ not done.")
    days = [dt.date.today() - dt.timedelta(days=i) for i in range(days_back-1, -1, -1)]
    cols = st.columns(7)
    for i, d in enumerate(days):
        done = d.isoformat() in done_days
        with cols[i % 7]:
            st.markdown(f"{'🟩' if done else '⬛'} {d.strftime('%d %b')}", unsafe_allow_html=True)

def img_to_data_uri(mime: str, b64: str) -> str:
    return f"data:{mime};base64,{b64}"

def image_slider_html(before_uri: str, after_uri: str, height: int = 380) -> str:
    # Pure HTML/CSS/JS compare slider (no external deps)
    return f"""
<div style="max-width:900px;">
  <div style="position:relative; width:100%; height:{height}px; overflow:hidden; border-radius:12px; border:1px solid rgba(0,0,0,0.12);">
    <img src="{before_uri}" style="position:absolute; inset:0; width:100%; height:100%; object-fit:cover;" />
    <div id="clipWrap" style="position:absolute; inset:0; width:50%; overflow:hidden;">
      <img src="{after_uri}" style="position:absolute; inset:0; width:100%; height:100%; object-fit:cover;" />
    </div>
    <input id="rng" type="range" min="0" max="100" value="50"
      style="position:absolute; left:12px; right:12px; bottom:12px; width:calc(100% - 24px);" />
  </div>
  <div style="display:flex; justify-content:space-between; margin-top:8px; font-size:13px; opacity:0.75;">
    <div>Before</div><div>After</div>
  </div>
</div>
<script>
  const rng = document.getElementById("rng");
  const wrap = document.getElementById("clipWrap");
  rng.addEventListener("input", () => {{
    wrap.style.width = rng.value + "%";
  }});
</script>
"""

# ---------- Sidebar ----------
st.sidebar.markdown(f"## {APP_TITLE}")
st.sidebar.caption("Build the habit. Kill the chaos. Not medical advice.")

page = st.sidebar.radio("Navigate", ["Today", "Photos", "Progress", "Reminders", "Help"], index=0)

st.sidebar.markdown("---")
st.sidebar.caption("Data control")
if st.sidebar.button("Export my data (ZIP)"):
    # Build ZIP in memory: CSV + photos
    df = get_all_daily()
    photos_df = list_photos()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("daily_log.csv", df.to_csv(index=False))
        if not photos_df.empty:
            z.writestr("photos_index.csv", photos_df.drop(columns=["data_b64"]).to_csv(index=False))
            for _, r in photos_df.iterrows():
                fname = r["filename"] or f"{r['kind']}_{r['day']}_{r['id']}.jpg"
                ext = os.path.splitext(fname)[1] or ".jpg"
                safe = f"photos/{r['kind']}/{r['day']}_{r['id']}{ext}"
                z.writestr(safe, base64.b64decode(r["data_b64"]))
    buffer.seek(0)
    st.sidebar.download_button("Download ZIP", data=buffer.getvalue(), file_name="naillock_export.zip", mime="application/zip")

uploaded_zip = st.sidebar.file_uploader("Import data (ZIP)", type=["zip"], help="Restore from a NailLock export.")
if uploaded_zip:
    try:
        zf = zipfile.ZipFile(uploaded_zip)
        # daily_log
        if "daily_log.csv" in zf.namelist():
            df = pd.read_csv(zf.open("daily_log.csv"))
            for _, r in df.iterrows():
                upsert_daily(
                    str(r["day"]),
                    int(r.get("did_treatment",0)),
                    int(r.get("washed_dried",0)),
                    int(r.get("fresh_socks",0)),
                    int(r.get("shoes_aired",0)),
                    str(r.get("notes","") or "")
                )
        # photos: import any files under photos/
        for name in zf.namelist():
            if name.startswith("photos/") and not name.endswith("/"):
                parts = name.split("/")
                # photos/kind/...
                if len(parts) >= 3:
                    kind = parts[1]
                    blob = zf.read(name)
                    # try infer day from filename prefix
                    day_guess = parts[2].split("_")[0]
                    add_photo(day_guess if len(day_guess)==10 else today_iso(), kind, os.path.basename(name), "image/jpeg", blob)
        st.sidebar.success("Import complete.")
    except Exception as e:
        st.sidebar.error(f"Import failed: {e}")

# ---------- Header ----------
st.markdown(f"<div class='nl-hero'>🦶 {APP_TITLE}</div>", unsafe_allow_html=True)
st.markdown(f"<div class='nl-sub'>{APP_TAGLINE}</div>", unsafe_allow_html=True)
st.markdown("<div class='nl-small nl-muted'>If you have pain, spreading redness, drainage, fever, diabetes/poor circulation, or you’re unsure: get medical advice.</div>", unsafe_allow_html=True)
st.divider()

# ---------- Load stats ----------
df_all = get_all_daily()
streak, done_days = streak_and_done_days(df_all)

# ---------- Hero metrics ----------
m1, m2, m3, m4 = st.columns([1,1,2,2])
with m1:
    st.metric("Streak", f"{streak} days")
with m2:
    st.metric("Level", level_for(streak))
with m3:
    st.metric("Badge", badge_for(streak))
with m4:
    target = 90
    st.progress(min(streak/target, 1.0), text=f"Mission: {streak}/{target} days")

render_heatmap(done_days, days_back=35)

# ---------- Aurosense story (your voice: blunt, human, real) ----------
with st.expander("The story (why this exists)"):
    st.markdown(
        """
**This is a real, embarrassing, stubborn human problem.**  
Not because people are lazy. Because the system is set up to make you fail:

- nothing looks like it’s changing for weeks  
- you miss days, then you mentally “restart”  
- shoes re-infect you quietly  
- you stop early because you’re fed up

So NailLock does one thing properly:

**It turns a slow, invisible grind into a visible mission.**  
Today is all that matters. You log it. The streak builds. The proof accumulates.

No shame. No fluff. No pretending. Just a clean system.
"""
    )

# ---------- Pages ----------
if page == "Today":
    st.subheader("Today")
    day = today_iso()
    row = get_daily(day)

    c1, c2 = st.columns([2,1])

    with c1:
        st.markdown("<div class='nl-card'>", unsafe_allow_html=True)
        st.markdown("### Today’s actions")
        did_treatment = st.checkbox("Applied treatment (your choice / clinician plan)", value=bool(row["did_treatment"]))
        washed_dried = st.checkbox("Washed + dried properly (especially between toes)", value=bool(row["washed_dried"]))
        fresh_socks = st.checkbox("Fresh socks", value=bool(row["fresh_socks"]))
        shoes_aired = st.checkbox("Shoes aired / disinfected (reduce re-infection)", value=bool(row["shoes_aired"]))

        notes = st.text_area("Notes (optional)", value=row["notes"], height=90, placeholder="Anything worth remembering today?")

        score = int(did_treatment) + int(washed_dried) + int(fresh_socks) + int(shoes_aired)
        st.markdown(f"**Score:** {score}/4")

        save = st.button("✅ Save today", use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

        if save:
            upsert_daily(day, int(did_treatment), int(washed_dried), int(fresh_socks), int(shoes_aired), notes)
            st.success("Saved. Don’t negotiate with tomorrow. You did today.")

    with c2:
        st.markdown("<div class='nl-card'>", unsafe_allow_html=True)
        st.markdown("### Quick guidance (non-medical)")
        st.markdown(
            """
- **Short nails help.** Thick infected nail is a barrier.
- **Shoes matter.** If you treat the nail but re-seed from shoes, it drags on.
- **Consistency beats intensity.** The win is days stacked.

If you want **real confirmation** it's fungal, or it’s spreading / painful — get it checked.
"""
        )
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    st.subheader("Tonight’s move")
    st.write("Pick one shoe habit and lock it: rotate shoes, use antifungal powder/spray, dry fully, don’t trap moisture.")

elif page == "Photos":
    st.subheader("Photos (proof beats feelings)")
    st.caption("Photos are stored inside this app’s database. On Streamlit Cloud, storage can reset sometimes. Use Export ZIP to keep your proof.")

    pcol1, pcol2 = st.columns([1,1])

    with pcol1:
        st.markdown("<div class='nl-card'>", unsafe_allow_html=True)
        st.markdown("### Add photos")
        kind = st.selectbox("Type", ["before", "after", "weekly"], index=2)
        day = st.date_input("Date", value=dt.date.today())
        up = st.file_uploader("Upload image", type=["jpg","jpeg","png"])
        if st.button("Upload", disabled=(up is None)):
            try:
                data = up.getvalue()
                mime = up.type or "image/jpeg"
                add_photo(day.isoformat(), kind, up.name, mime, data)
                st.success("Photo saved.")
            except Exception as e:
                st.error(str(e))
        st.markdown("</div>", unsafe_allow_html=True)

    with pcol2:
        st.markdown("<div class='nl-card'>", unsafe_allow_html=True)
        st.markdown("### Before / After compare")
        photos_df = list_photos()
        if photos_df.empty:
            st.info("No photos yet.")
        else:
            befores = photos_df[photos_df["kind"]=="before"]
            afters = photos_df[photos_df["kind"]=="after"]

            def pick(df: pd.DataFrame, label: str):
                if df.empty:
                    return None
                options = [(int(r["id"]), f"{r['day']} — {r['filename'] or r['kind']}") for _, r in df.iterrows()]
                chosen = st.selectbox(label, options, format_func=lambda x: x[1])
                pid = chosen[0]
                rec = df[df["id"]==pid].iloc[0].to_dict()
                return rec

            b = pick(befores, "Select BEFORE")
            a = pick(afters, "Select AFTER")

            if b and a:
                before_uri = img_to_data_uri(b["mime"], b["data_b64"])
                after_uri = img_to_data_uri(a["mime"], a["data_b64"])
                # HTML slider (no extra packages)
                st.components.v1.html(image_slider_html(before_uri, after_uri, height=420), height=520)
            else:
                st.info("Add at least one 'before' and one 'after' photo to compare.")
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    st.subheader("Photo library")
    photos_df = list_photos()
    if photos_df.empty:
        st.info("No photos saved.")
    else:
        # Show small gallery grouped
        for kind in ["before","weekly","after"]:
            sub = photos_df[photos_df["kind"]==kind]
            if sub.empty:
                continue
            st.markdown(f"### {kind.title()}")
            grid = st.columns(4)
            for i, (_, r) in enumerate(sub.iterrows()):
                with grid[i % 4]:
                    st.image(base64.b64decode(r["data_b64"]), caption=f"{r['day']} — {r['filename'] or ''}", use_container_width=True)
                    if st.button(f"Delete #{int(r['id'])}", key=f"del_{int(r['id'])}"):
                        delete_photo(int(r["id"]))
                        st.rerun()

elif page == "Progress":
    st.subheader("Progress")
    df = get_all_daily()
    if df.empty:
        st.info("No logs yet. Go to Today and save your first day.")
    else:
        df["score"] = df.apply(lambda r: int(r["did_treatment"])+int(r["washed_dried"])+int(r["fresh_socks"])+int(r["shoes_aired"]), axis=1)
        df["date"] = pd.to_datetime(df["day"])
        df = df.sort_values("date")

        c1, c2 = st.columns([2,1])
        with c1:
            st.markdown("<div class='nl-card'>", unsafe_allow_html=True)
            st.markdown("### Scores over time")
            st.line_chart(df.set_index("date")["score"])
            st.markdown("</div>", unsafe_allow_html=True)

        with c2:
            st.markdown("<div class='nl-card'>", unsafe_allow_html=True)
            st.markdown("### Summary")
            total_days = df.shape[0]
            done_days_count = int((df["score"]>0).sum())
            avg_score = float(df["score"].mean()) if total_days else 0.0
            st.metric("Logged days", total_days)
            st.metric("Done days", done_days_count)
            st.metric("Average score", f"{avg_score:.2f}/4")
            st.markdown("</div>", unsafe_allow_html=True)

        st.divider()
        st.subheader("Journal")
        st.dataframe(df[["day","score","did_treatment","washed_dried","fresh_socks","shoes_aired","notes"]], use_container_width=True)

elif page == "Reminders":
    st.subheader("Reminders (realistic, works everywhere)")
    st.caption("A web app can’t reliably push alerts to your phone by magic. So we do the thing that actually works: a Calendar reminder file (.ics).")

    st.markdown("<div class='nl-card'>", unsafe_allow_html=True)
    t = st.time_input("Reminder time", value=dt.time(9, 0))
    days = st.number_input("How many days?", min_value=7, max_value=365, value=90)
    title = st.text_input("Reminder title", value="NailLock — daily actions")
    ics = make_daily_ics(title=title, hour=t.hour, minute=t.minute, days=int(days))
    st.download_button("Download calendar reminder (.ics)", data=ics, file_name="naillock_reminder.ics", mime="text/calendar", use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    st.subheader("Daily script (what you do when the reminder hits)")
    st.markdown(
        """
- Do the actions (2–5 minutes)
- Log “Today” in NailLock
- If you’re tired: **do the minimum** — but don’t break the chain
"""
    )

elif page == "Help":
    st.subheader("Help / FAQs")
    st.markdown("<div class='nl-card'>", unsafe_allow_html=True)
    st.markdown(
        """
### What this is
A **consistency engine** for a slow condition where people quit early because progress is invisible.

### What this is NOT
Medical advice. A diagnosis. A cure claim.

### When to get medical help
- pain, swelling, heat, drainage
- spreading redness
- fever
- diabetes / circulation issues
- uncertainty if it’s fungal or something else

### Why “shoes” are part of this
Re-infection and moisture are common reasons people stall. NailLock pushes the full loop, not just the nail.

### Data privacy
This app stores data in a small local database file. On Streamlit Cloud, this can reset sometimes. Use **Export ZIP** to keep your proof.
"""
    )
    st.markdown("</div>", unsafe_allow_html=True)
