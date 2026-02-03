import streamlit as st
import pandas as pd
from datetime import date, datetime
import os
from PIL import Image

st.set_page_config(page_title="NailLock", layout="centered")

DATA_FILE = "data.csv"
FEEDBACK_FILE = "feedback.csv"
IMG_DIR = "photos"

os.makedirs(IMG_DIR, exist_ok=True)

# ---------- INIT STORAGE ----------
if not os.path.exists(DATA_FILE):
    pd.DataFrame(columns=[
        "date","mode","score","streak","level",
        "medication","topical","thin","shoes","socks",
        "notes"
    ]).to_csv(DATA_FILE, index=False)

if not os.path.exists(FEEDBACK_FILE):
    pd.DataFrame(columns=["timestamp","message"]).to_csv(FEEDBACK_FILE, index=False)

df = pd.read_csv(DATA_FILE)
fb = pd.read_csv(FEEDBACK_FILE)


# ---------- LOGIC ----------
TASKS_BY_MODE = {
    "Basic": [
        ("Topical applied", "topical", 25),
        ("Nails thinned/filed", "thin", 25),
        ("Fresh socks", "socks", 25),
        ("Shoes disinfected", "shoes", 25),
    ],
    "Standard": [
        ("Medication taken (if prescribed)", "medication", 20),
        ("Topical applied", "topical", 20),
        ("Nails thinned/filed", "thin", 20),
        ("Shoes disinfected", "shoes", 20),
        ("Fresh socks", "socks", 20),
    ],
    "Extreme": [
        ("Medication taken (if prescribed)", "medication", 20),
        ("Topical applied", "topical", 20),
        ("Nails thinned/filed (proper)", "thin", 20),
        ("Shoes disinfected", "shoes", 20),
        ("Fresh socks", "socks", 20),
        # Extreme mode “extra discipline” is enforced by stricter scoring rules below
    ],
}

def compute_score(mode: str, checks: dict) -> int:
    tasks = TASKS_BY_MODE[mode]
    score = 0
    for _, key, pts in tasks:
        if checks.get(key, False):
            score += pts
    return min(score, 100)

def compute_streak(prev_streak: int, score: int, mode: str) -> int:
    # Extreme is stricter: needs >= 90 to continue streak.
    threshold = 90 if mode == "Extreme" else 80
    return (prev_streak + 1) if score >= threshold else 0

def get_level(streak: int) -> str:
    if streak < 5: return "Detection"
    if streak < 14: return "Breakthrough"
    if streak < 30: return "Suppression"
    if streak < 60: return "Clearance"
    return "Clean"

def save_day(row: dict):
    global df
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(DATA_FILE, index=False)

def save_feedback(message: str):
    global fb
    new = {"timestamp": datetime.utcnow().isoformat(), "message": message.strip()}
    fb = pd.concat([fb, pd.DataFrame([new])], ignore_index=True)
    fb.to_csv(FEEDBACK_FILE, index=False)


# ---------- HEADER / STORY ----------
st.title("🦶 NailLock™")
st.caption("A guided consistency system for fungal nails. Not medical advice.")

with st.expander("The story (why this exists)", expanded=True):
    st.write(
        "Most people don’t fail because the treatments don’t exist — they fail because of gaps: "
        "missed days, stopping early, not disinfecting shoes, and no visible progress.\n\n"
        "NailLock turns this into a mission: a daily plan, a score, a streak, and weekly photo tracking.\n\n"
        "Use it free. If it helps, share it."
    )

with st.expander("Why this exists", expanded=True):
    st.write("""
There isn’t a magic cure.  
There’s consistency.

Most people don’t fail because treatments don’t work.  
They fail because they stop.

So I built something that stops you stopping.

That’s NailLock.
""")

st.divider()

# ---------- CURRENT STATUS ----------
if len(df) > 0:
    last = df.iloc[-1]
    current_score = int(last["score"])
    current_streak = int(last["streak"])
    current_level = last["level"]
    current_mode = last["mode"]
else:
    current_score, current_streak, current_level, current_mode = 0, 0, "Detection", "Standard"

c1, c2, c3, c4 = st.columns(4)
c1.metric("Score", f"{current_score}/100")
c2.metric("Streak", f"{current_streak} days")
c3.metric("Level", current_level)
c4.metric("Mode", current_mode)

st.divider()


# ---------- MODE SELECT ----------
st.header("Choose your mode")
mode = st.radio("Mode", ["Basic", "Standard", "Extreme"], index=["Basic","Standard","Extreme"].index(current_mode))

st.caption(
    "Basic = topical + hygiene. Standard = typical plan. Extreme = stricter streak rules + zero gaps mindset."
)

st.divider()


# ---------- TODAY'S MISSION ----------
st.header("Today’s Mission")

tasks = TASKS_BY_MODE[mode]
checks = {}

for label, key, pts in tasks:
    checks[key] = st.checkbox(f"{label} (+{pts})")

notes = st.text_area("Notes (optional)", placeholder="Anything relevant: missed because…, shoe rotation, symptoms, etc.")

colA, colB = st.columns(2)

with colA:
    if st.button("Complete Day"):
        score = compute_score(mode, checks)
        prev_streak = current_streak
        streak = compute_streak(prev_streak, score, mode)
        level = get_level(streak)

        row = {
            "date": str(date.today()),
            "mode": mode,
            "score": score,
            "streak": streak,
            "level": level,
            "medication": int(checks.get("medication", False)),
            "topical": int(checks.get("topical", False)),
            "thin": int(checks.get("thin", False)),
            "shoes": int(checks.get("shoes", False)),
            "socks": int(checks.get("socks", False)),
            "notes": notes.strip()
        }
        save_day(row)
        st.success(f"Logged. Score {score}/100. Streak now {streak}.")
        st.rerun()

with colB:
    if st.button("Missed Today (log 0)"):
        row = {
            "date": str(date.today()),
            "mode": mode,
            "score": 0,
            "streak": 0,
            "level": get_level(0),
            "medication": 0, "topical": 0, "thin": 0, "shoes": 0, "socks": 0,
            "notes": (notes.strip() or "Missed day logged.")
        }
        save_day(row)
        st.warning("Missed day logged. Streak reset to 0.")
        st.rerun()

st.divider()


# ---------- WEEKLY PHOTO + HISTORY ----------
st.header("Weekly Photo Log")

img = st.file_uploader("Upload nail photo (jpg/png)", type=["png", "jpg", "jpeg"])
if img:
    im = Image.open(img)
    filepath = os.path.join(IMG_DIR, f"{date.today()}.png")
    im.save(filepath)
    st.image(im, width=280)
    st.success("Photo saved.")

# Show recent photos
photos = sorted([p for p in os.listdir(IMG_DIR) if p.lower().endswith(".png")], reverse=True)
if photos:
    st.caption("Recent photos (newest first):")
    for p in photos[:6]:
        st.write(f"- {p.replace('.png','')}")
else:
    st.caption("No photos yet.")

st.divider()


# ---------- PROGRESS + RISK ----------
st.header("Progress")

if len(df) > 0:
    # Chart
    tmp = df.copy()
    tmp["date"] = pd.to_datetime(tmp["date"], errors="coerce")
    tmp = tmp.dropna(subset=["date"]).sort_values("date")

    st.line_chart(tmp.set_index("date")["score"])

    # Risk logic: last 7-day average
    last7 = tmp.tail(7)
    if len(last7) >= 4:
        avg7 = last7["score"].mean()
        if avg7 < 60:
            st.error("⚠️ Relapse risk rising (7-day average low). You’re leaving gaps. Tighten the mission.")
        elif avg7 < 75:
            st.warning("⚠️ You’re borderline. Push consistency to prevent relapse.")
        else:
            st.success("✅ Consistency looks strong. Keep going.")

    with st.expander("Log history"):
        st.dataframe(tmp[["date","mode","score","streak","level","notes"]], use_container_width=True)
else:
    st.write("No logs yet.")

st.divider()


# ---------- EXPORT ----------
st.header("Export")

col1, col2 = st.columns(2)

with col1:
    st.download_button(
        "Download logs (CSV)",
        data=pd.read_csv(DATA_FILE).to_csv(index=False).encode("utf-8"),
        file_name="naillock_logs.csv",
        mime="text/csv",
    )

with col2:
    st.caption("Photos are stored in the /photos folder on the machine/server.")


st.divider()


# ---------- FEEDBACK ----------
st.header("Feedback (takes 10 seconds)")
msg = st.text_area("Tell me what to improve / what annoyed you / what you want next", placeholder="Be blunt.")
if st.button("Send feedback"):
    if msg.strip():
        save_feedback(msg)
        st.success("Saved. Thank you.")
    else:
        st.warning("Write something first.")

st.caption("This stores feedback locally (feedback.csv).")

