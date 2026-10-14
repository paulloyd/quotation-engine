"""Review console for the quotation engine.   streamlit run app.py"""
import json, pathlib
import pandas as pd, streamlit as st
from engine.db import init_db
from engine.pipeline import (parse_email, process_rfq, get_quote, approve_quote, request_info,
                             reject_quote, draft_quote_email)

st.set_page_config(page_title="Quotation Engine", layout="wide")
SAMPLES = pathlib.Path(__file__).parent / "data" / "sample_rfqs"

@st.cache_resource
def get_conn(): return init_db()
conn = get_conn()

st.sidebar.title("Reviewer")
who = st.sidebar.text_input("Your name", "analyst")
role = st.sidebar.selectbox("Role", ["reviewer", "manager"], help="Quotes >= $25,000 need a manager.")
if st.sidebar.button("Load 8 sample RFQs"):
    for f in sorted(SAMPLES.glob("*.txt")):
        s, subj, b = parse_email(f.read_text()); process_rfq(conn, s, b, subj)
    st.rerun()
if st.sidebar.button("Reset demo data"):
    for t in ("outbox", "audit_log", "quote_lines", "quotes"): conn.execute(f"DELETE FROM {t}")
    conn.commit(); st.rerun()

st.title("Quotation Engine - review queue")
st.caption("Nothing reaches a customer until a human approves it. Pricing is deterministic; AI only reads and drafts.")
tab_q, tab_new, tab_impact = st.tabs(["Review queue", "Submit an RFQ", "Impact"])

with tab_q:
    df = pd.read_sql("SELECT id, status, sender, incoterm, destination, total_usd, flags, extraction_method AS method FROM quotes ORDER BY id DESC", conn)
    if df.empty:
        st.info("Queue is empty - use **Load 8 sample RFQs** in the sidebar.")
    else:
        st.dataframe(df, width="stretch", hide_index=True)
        qid = st.selectbox("Open quote", df["id"].tolist(), format_func=lambda i: f"#{i} - {df.set_index('id').loc[i, 'status']}")
        q, lines = get_quote(conn, qid)
        c1, c2, c3 = st.columns(3)
        c1.metric("Status", q["status"]); c2.metric("Total (USD)", f"{q['total_usd']:,.2f}" if q["total_usd"] else "-")
        c3.metric("Extraction confidence", q["extraction_confidence"])
        for f in filter(None, q["flags"].split(",")): st.warning(f)
        st.dataframe(pd.DataFrame(lines).drop(columns=["id", "quote_id"]), width="stretch", hide_index=True)
        if q["breakdown"]:
            st.write("**Cost build-up (USD):**", json.loads(q["breakdown"]))
        with st.expander("Original email"): st.text(q["raw_email"])
        if q["status"] in ("DRAFT", "NEEDS_REVIEW"):
            with st.expander("Email that will be sent on approval", expanded=True): st.text(draft_quote_email(conn, qid))
        a, b, c = st.columns(3)
        if a.button("Approve & send", disabled=q["status"] not in ("DRAFT", "NEEDS_REVIEW")):
            try: approve_quote(conn, qid, who, role); st.success("Approved. Email written to outbox (mock send)."); st.rerun()
            except PermissionError as e: st.error(str(e))
        if b.button("Request info from customer", disabled=q["status"] != "NEEDS_INFO"):
            request_info(conn, qid, who); st.rerun()
        if c.button("Reject", disabled=q["status"] in ("SENT", "REJECTED")):
            reject_quote(conn, qid, who, "rejected in review"); st.rerun()
        st.write("**Audit trail**")
        st.dataframe(pd.read_sql("SELECT ts, actor, action, detail FROM audit_log WHERE quote_id=? ORDER BY id", conn, params=(qid,)),
                     width="stretch", hide_index=True)

with tab_new:
    sender = st.text_input("From", "dana@acmelighting.example")
    body = st.text_area("Email body", "Please quote 250 units of LED-FLD-50, CIF Savannah.", height=140)
    if st.button("Process RFQ"):
        qid = process_rfq(conn, sender, body); st.success(f"Created quote #{qid} - see the Review queue tab.")

with tab_impact:
    st.caption("Baseline = average prep minutes for the same number of lines in the SYNTHETIC quote history. "
               "Review time is an assumption you control - this is an estimate, not a measured result.")
    review_min = st.slider("Human review minutes per quote", 1, 20, 5)
    r = conn.execute("""SELECT COUNT(*) n, SUM(CASE WHEN status='DRAFT' THEN 1 ELSE 0 END) clean,
        SUM((SELECT AVG(prep_minutes) FROM quote_history h WHERE h.line_count = (SELECT COUNT(*) FROM quote_lines l WHERE l.quote_id = quotes.id))) base
        FROM quotes""").fetchone()
    if r["n"]:
        base = r["base"] or 0; saved = base - r["n"] * review_min
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Quotes processed", r["n"]); m2.metric("Straight to approval (no flags)", f"{r['clean']}/{r['n']}")
        m3.metric("Est. manual minutes", f"{base:,.0f}"); m4.metric("Est. minutes saved", f"{saved:,.0f}")
    else:
        st.info("Process some RFQs first.")
