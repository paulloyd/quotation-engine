"""RFQ -> priced draft quote -> human approval -> (mock) send. Every step is audit-logged."""
import json, time, datetime as dt
from email.utils import parseaddr
from .db import load_reference
from .extract import extract
from .pricing import price_quote

HIGH_VALUE_USD = 25000
DEFAULT_MARGIN = 0.25                      # provisional margin for unknown customers
BLOCKING = {"UNKNOWN_SKU", "MISSING_QTY", "NO_LINES_FOUND", "UNKNOWN_CUSTOMER", "UNSUPPORTED_LANE"}

def now(): return dt.datetime.now().replace(microsecond=0).isoformat(sep=" ")

def audit(conn, qid, actor, action, detail=""):
    conn.execute("INSERT INTO audit_log (quote_id, ts, actor, action, detail) VALUES (?,?,?,?,?)",
                 (qid, now(), actor, action, detail))

def parse_email(text):
    """Minimal 'From:/Subject:' header parser for the sample files."""
    head, _, body = text.partition("\n\n")
    hdr = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in head.splitlines() if ":" in l}
    return hdr.get("from", ""), hdr.get("subject", ""), body.strip()

def process_rfq(conn, sender, body, subject="", force_rules=False):
    t0 = time.perf_counter()
    sender = parseaddr(sender)[1] or sender          # 'Dana <dana@x.com>' -> 'dana@x.com'
    ref = load_reference(conn)
    rfq = extract(body, ref["catalog"], force_rules=force_rules)
    flags = []
    cust = ref["customers"].get(sender.split("@")[-1].lower())
    if not cust: flags.append("UNKNOWN_CUSTOMER")
    margin = cust["margin_pct"] if cust else DEFAULT_MARGIN
    incoterm = rfq.incoterm or "CIF"
    if not rfq.incoterm: flags.append("ASSUMED_INCOTERM")
    dest = rfq.destination or "HOUSTON"
    if not rfq.destination and incoterm != "FOB": flags.append("ASSUMED_DESTINATION")
    if not rfq.lines: flags.append("NO_LINES_FOUND")
    if rfq.confidence < 0.7: flags.append("LOW_CONFIDENCE")

    priceable, line_flags = [], {}
    for i, l in enumerate(rfq.lines):
        lf = []
        if l.sku not in ref["catalog"]: lf.append("UNKNOWN_SKU")
        if l.qty is None: lf.append("MISSING_QTY")
        if not lf:
            if l.qty < ref["catalog"][l.sku]["moq"]: lf.append("BELOW_MOQ")
            priceable.append((l.sku, l.qty))
        line_flags[i] = lf
        flags += [f for f in lf if f not in flags]

    pricing = None
    if priceable:
        try:
            pricing = price_quote(priceable, ref["catalog"], ref["suppliers"], ref["fx"], ref["freight"],
                                  incoterm, dest, margin)
        except KeyError:
            flags.append("UNSUPPORTED_LANE")
    total = float(pricing["price_total"]) if pricing else None
    if total and total >= HIGH_VALUE_USD: flags.append("HIGH_VALUE")
    flags = list(dict.fromkeys(flags))
    status = "NEEDS_INFO" if set(flags) & BLOCKING else ("NEEDS_REVIEW" if flags else "DRAFT")

    cur = conn.execute(
        "INSERT INTO quotes (customer_id, sender, subject, incoterm, destination, status, extraction_method,"
        " extraction_confidence, created_at, total_usd, margin_pct, flags, breakdown, raw_email, process_ms)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cust["id"] if cust else None, sender, subject, incoterm, dest, status, rfq.method, rfq.confidence,
         now(), total, margin, ",".join(flags),
         json.dumps(pricing["components"], default=str) if pricing else None, body,
         int((time.perf_counter() - t0) * 1000)))
    qid = cur.lastrowid
    priced = {l["sku"]: l for l in pricing["lines"]} if pricing else {}
    for i, l in enumerate(rfq.lines):
        p = priced.get(l.sku) if not any(f in line_flags[i] for f in ("UNKNOWN_SKU", "MISSING_QTY")) else None
        conn.execute("INSERT INTO quote_lines (quote_id, sku, qty, unit_price_usd, line_total_usd, cost_usd, flags)"
                     " VALUES (?,?,?,?,?,?,?)",
                     (qid, l.sku, l.qty, float(p["unit_price"]) if p else None,
                      float(p["line_total"]) if p else None, float(p["cost"]) if p else None,
                      ",".join(line_flags[i])))
    audit(conn, qid, "system", "RFQ_RECEIVED", f"from {sender}")
    audit(conn, qid, "system", "EXTRACTED", f"method={rfq.method} confidence={rfq.confidence} {'; '.join(rfq.notes)}")
    audit(conn, qid, "system", "PRICED" if pricing else "NOT_PRICED", f"status={status} flags={','.join(flags) or '-'}")
    conn.commit()
    return qid

def get_quote(conn, qid):
    q = conn.execute("SELECT * FROM quotes WHERE id=?", (qid,)).fetchone()
    lines = conn.execute("SELECT * FROM quote_lines WHERE quote_id=?", (qid,)).fetchall()
    return dict(q), [dict(l) for l in lines]

def draft_quote_email(conn, qid):
    q, lines = get_quote(conn, qid)
    body = [f"Dear customer,", "", f"Thank you for your request. Our quotation ({q['incoterm']} {q['destination'] if q['incoterm'] != 'FOB' else 'origin port'}):", ""]
    for l in lines:
        body.append(f"  {l['sku']}  x {l['qty']:,}  @ ${l['unit_price_usd']:,.4f}  =  ${l['line_total_usd']:,.2f}")
    body += ["", f"TOTAL: ${q['total_usd']:,.2f} USD", "Validity: 14 days. Prices subject to supplier confirmation.", "", "Kind regards,", "Sales Desk"]
    return "\n".join(body)

def approve_quote(conn, qid, approver, role):
    q, _ = get_quote(conn, qid)
    if q["status"] not in ("DRAFT", "NEEDS_REVIEW"):
        raise ValueError(f"quote {qid} is {q['status']} and cannot be approved")
    if "HIGH_VALUE" in q["flags"].split(",") and role != "manager":
        raise PermissionError("quotes >= $25,000 need manager approval")
    body = draft_quote_email(conn, qid)
    conn.execute("INSERT INTO outbox (quote_id, to_addr, subject, body, created_at) VALUES (?,?,?,?,?)",
                 (qid, q["sender"], f"Quotation #{qid}", body, now()))
    conn.execute("UPDATE quotes SET status='SENT', approved_by=?, approved_at=? WHERE id=?", (approver, now(), qid))
    audit(conn, qid, approver, "APPROVED_AND_SENT", f"role={role} (mock send to outbox)")
    conn.commit()

def request_info(conn, qid, actor):
    q, lines = get_quote(conn, qid)
    issues = []
    for l in lines:
        for f in filter(None, l["flags"].split(",")):
            if f == "UNKNOWN_SKU": issues.append(f"- We could not find product code '{l['sku']}' in our catalogue.")
            if f == "MISSING_QTY": issues.append(f"- Please confirm the quantity for {l['sku']}.")
    fl = q["flags"].split(",")
    if "NO_LINES_FOUND" in fl: issues.append("- Please send the exact product codes and quantities you need.")
    if "UNKNOWN_CUSTOMER" in fl: issues.append("- Please confirm your company details so we can set up your account.")
    body = "Hello,\n\nTo prepare your quotation we need a little more information:\n" + "\n".join(issues) + "\n\nThank you,\nSales Desk"
    conn.execute("INSERT INTO outbox (quote_id, to_addr, subject, body, created_at) VALUES (?,?,?,?,?)",
                 (qid, q["sender"], f"Re: quotation request #{qid} - information needed", body, now()))
    conn.execute("UPDATE quotes SET status='AWAITING_CUSTOMER' WHERE id=?", (qid,))
    audit(conn, qid, actor, "INFO_REQUESTED", "clarification email queued")
    conn.commit()

def reject_quote(conn, qid, actor, reason=""):
    conn.execute("UPDATE quotes SET status='REJECTED' WHERE id=?", (qid,))
    audit(conn, qid, actor, "REJECTED", reason); conn.commit()
