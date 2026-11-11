"""דאשבורד: streamlit run dashboard.py"""
import json
import os

import streamlit as st

from hunter.config import load_config
from hunter.db import DB, STATUSES

st.set_page_config(page_title="House Hunter", page_icon="🏠", layout="wide")
st.markdown("<style>.main, .stMarkdown, .stTextArea, .stSelectbox {direction: rtl; text-align: right;}</style>",
            unsafe_allow_html=True)

cfg = load_config(os.environ.get("HUNTER_CONFIG"))
db = DB(cfg["db_path"])
rows = [dict(r) for r in db.all()]
for r in rows:
    r["d"] = json.loads(r["data"])
    r["a"] = json.loads(r["analysis"]) if r["analysis"] else {}

st.title("🏠 House Hunter")
active = [r for r in rows if r["verdict"] in ("match", "check")]
c = st.columns(5)
c[0].metric("התאמות מלאות", sum(r["verdict"] == "match" for r in active))
c[1].metric("דורשות בירור", sum(r["verdict"] == "check" for r in active))
c[2].metric("חדשות (לא נצפו)", sum(r["status"] == "new" for r in active))
c[3].metric("יצרתי קשר", sum(r["status"] in ("contacted", "visit", "visited") for r in rows))
c[4].metric("ממתינות לבדיקה", sum(not r["details_fetched"] and r["verdict"] != "reject" for r in rows))

with st.sidebar:
    st.header("סינון")
    verdicts = st.multiselect("התאמה", ["match", "check", "reject"], default=["match", "check"],
                              format_func={"match": "✅ מתאים", "check": "🟡 לבירור", "reject": "❌ נפסל"}.get)
    statuses = st.multiselect("סטטוס", list(STATUSES), default=[k for k in STATUSES if k != "not_relevant"],
                              format_func=STATUSES.get)
    cities = sorted({r["d"].get("city", "") for r in rows if r["d"].get("city")})
    city_sel = st.multiselect("עיר", cities, default=cities)
    only_priority = st.checkbox("רק שכונות מועדפות")
    sort_by = st.radio("מיון", ["ציון", "חדש ביותר", "מחיר"], horizontal=True)
    runs = db.last_runs(5)
    if runs:
        st.caption("ריצות אחרונות")
        for run in runs:
            st.caption(f"{run['finished']} · {run['seen']} נסרקו · {run['new']} חדשות"
                       + (f" · ⚠ {run['error']}" if run["error"] else ""))

view = [r for r in rows
        if (r["verdict"] in verdicts or (r["verdict"] is None and "check" in verdicts))
        and r["status"] in statuses and r["d"].get("city") in city_sel
        and (not only_priority or r["a"].get("priority"))]
if sort_by == "חדש ביותר":
    view.sort(key=lambda r: r["first_seen"], reverse=True)
elif sort_by == "מחיר":
    view.sort(key=lambda r: r["price"] or 99999)
st.caption(f"{len(view)} מודעות")

for r in view[:200]:
    d, a = r["d"], r["a"]
    icon = {"match": "✅", "check": "🟡", "reject": "❌"}.get(r["verdict"], "⏳")
    star = "⭐ " if a.get("priority") else ""
    price = f"{r['price']:,} ₪" if r["price"] else "?"
    title = f"{icon} {star}{d.get('street', '')} {d.get('house_number') or ''}, {d.get('neighborhood', '')}, " \
            f"{d.get('city', '')} — {price} · {d.get('rooms')} חד' · קומה {d.get('floor')} · [{STATUSES[r['status']]}]"
    with st.expander(title, expanded=False):
        left, right = st.columns([1, 2])
        with left:
            if d.get("images"):
                st.image(d["images"][0], width="stretch")
            st.link_button("פתח מודעה ביד 2", r["url"])
        with right:
            if a.get("summary"):
                st.write(a["summary"])
            info = [f"{d.get('sqm') or '?'} מ\"ר",
                    f"קומה {d.get('floor')}/{d.get('top_floor') or '?'}",
                    {True: "מעלית ✔", False: "בלי מעלית", None: "מעלית ?"}[d.get("elevator")],
                    {True: "חניה ✔", False: "בלי חניה", None: "חניה ?"}[d.get("parking")],
                    "תיווך" if d.get("is_broker") else "פרטי",
                    f"כניסה {d.get('entrance_date') or '?'}",
                    f"נמצא {r['first_seen'][:16].replace('T', ' ')}"]
            st.write(" · ".join(info))
            if a.get("pluses"):
                st.success(" · ".join(a["pluses"]))
            if a.get("flags"):
                st.warning("לבדוק: " + " · ".join(a["flags"]))
            if a.get("reasons"):
                st.error("נפסל: " + " · ".join(a["reasons"]))
            hist = json.loads(r["price_history"])
            if len(hist) > 1:
                st.caption("היסטוריית מחיר: " + " ← ".join(f"{p:,} ({t})" for t, p in hist))
            if d.get("description"):
                st.caption(d["description"])
            with st.form(key=f"f_{r['key']}"):
                status = st.selectbox("סטטוס", list(STATUSES), index=list(STATUSES).index(r["status"]),
                                      format_func=STATUSES.get)
                notes = st.text_area("הערות (עם מי דיברתי, מתי, מה נאמר)", value=r["notes"])
                if st.form_submit_button("שמור"):
                    db.set_status(r["key"], status, notes)
                    st.rerun()
        if r["status"] == "new":
            if st.button("סמן כנצפה", key=f"seen_{r['key']}"):
                db.set_status(r["key"], "seen")
                st.rerun()
