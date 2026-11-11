"""דאשבורד: streamlit run dashboard.py"""
import json
import os

import streamlit as st

from hunter.config import load_config
from hunter.db import DB, STATUSES
from hunter.models import Listing

SOURCES = {"yad2": "יד 2", "fb_group": "קבוצות פייסבוק", "fb_market": "Marketplace"}
PARKING = {"private": "🚗 חניה פרטית", "street_easy": "🚗 חניה בשפע ברחוב", "street_hard": "חניה קשה",
           "none": "בלי חניה", None: "חניה ?"}

st.set_page_config(page_title="House Hunter", page_icon="🏠", layout="wide")
st.markdown("<style>.main, .stMarkdown, .stTextArea, .stSelectbox {direction: rtl; text-align: right;}</style>",
            unsafe_allow_html=True)

cfg = load_config(os.environ.get("HUNTER_CONFIG"))
db = DB(cfg["db_path"])
all_rows = [dict(r) for r in db.all()]
dups: dict[str, list] = {}
for r in all_rows:
    r["d"] = json.loads(r["data"])
    r["a"] = json.loads(r["analysis"]) if r["analysis"] else {}
    r["d"]["city"] = r["d"].get("city") or "לא ידוע"
    if r["dup_of"]:
        dups.setdefault(r["dup_of"], []).append(r)
rows = [r for r in all_rows if not r["dup_of"]]  # one card per apartment

st.title("🏠 House Hunter")
active = [r for r in rows if r["verdict"] in ("match", "check")]
c = st.columns(6)
c[0].metric("התאמות מלאות", sum(r["verdict"] == "match" for r in active))
c[1].metric("דורשות בירור", sum(r["verdict"] == "check" for r in active))
c[2].metric("חדשות (לא נצפו)", sum(r["status"] == "new" for r in active))
c[3].metric("יצרתי קשר", sum(r["status"] in ("contacted", "visit", "visited") for r in rows))
c[4].metric("ממתינות לבדיקה", sum(not r["details_fetched"] and r["verdict"] != "reject" for r in rows))
c[5].metric("כפילויות שאוחדו", sum(len(v) for v in dups.values()))

with st.sidebar:
    st.header("סינון")
    verdicts = st.multiselect("התאמה", ["match", "check", "reject"], default=["match", "check"],
                              format_func={"match": "✅ מתאים", "check": "🟡 לבירור", "reject": "❌ נפסל"}.get)
    statuses = st.multiselect("סטטוס", list(STATUSES), default=[k for k in STATUSES if k != "not_relevant"],
                              format_func=STATUSES.get)
    src_sel = st.multiselect("מקור", list(SOURCES), default=list(SOURCES), format_func=SOURCES.get)
    cities = sorted({r["d"]["city"] for r in rows})
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
        and r["status"] in statuses and r["d"]["city"] in city_sel
        and (r["source"] in src_sel or any(x["source"] in src_sel for x in dups.get(r["key"], [])))
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
    listing = Listing.from_dict(d)
    others = dups.get(r["key"], [])
    where = listing.address or (a.get("summary") or d.get("description", ""))[:60]
    multi = f" · 📢 {len(others) + 1} פרסומים" if others else ""
    title = (f"{icon} {star}{where} — {price} · {d.get('rooms') or '?'} חד' · קומה "
             f"{d.get('floor') if d.get('floor') is not None else '?'} · {SOURCES.get(r['source'], '')}{multi} · "
             f"[{STATUSES[r['status']]}]")
    with st.expander(title, expanded=False):
        left, right = st.columns([1, 2])
        with left:
            if d.get("images"):
                st.image(d["images"][0], width="stretch")
            st.link_button(f"פתח ב{listing.source_name}", r["url"])
            for o in others:
                ol = Listing.from_dict(o["d"])
                price_note = f" ({o['price']:,} ₪)" if o["price"] and o["price"] != r["price"] else ""
                st.markdown(f"↪ [{ol.source_name}]({o['url']}){price_note}  \n"
                            f"<small>{o['dup_reason']}</small>", unsafe_allow_html=True)
        with right:
            if a.get("summary"):
                st.write(a["summary"])
            info = [f"{d.get('sqm') or '?'} מ\"ר",
                    f"קומה {d.get('floor')}/{d.get('top_floor') or '?'}",
                    {True: "מעלית ✔", False: "בלי מעלית", None: "מעלית ?"}[d.get("elevator")],
                    PARKING.get(a.get("parking"), "חניה ?"),
                    {True: "תיווך", False: "פרטי", None: "תיווך ?"}[d.get("is_broker")],
                    f"כניסה {d.get('entrance_date') or '?'}",
                    f"נמצא {r['first_seen'][:16].replace('T', ' ')}"]
            if d.get("phone"):
                info.append(f"📞 {d['phone']}")
            st.write(" · ".join(info))
            if a.get("pluses"):
                st.success(" · ".join(a["pluses"]))
            if a.get("flags"):
                st.warning("לבדוק: " + " · ".join(a["flags"]))
            if a.get("warnings"):
                st.info("⚠ " + " · ".join(a["warnings"]))
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
