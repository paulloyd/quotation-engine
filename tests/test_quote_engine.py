import sys, pathlib, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.db import init_db, load_reference
from engine.extract import extract_rules, _normalize_llm_json
from engine.pipeline import parse_email, process_rfq, get_quote, approve_quote, request_info, reject_quote
from engine.pricing import price_quote

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_rfqs"

@pytest.fixture()
def conn(tmp_path):
    return init_db(tmp_path / "t.db", rebuild=True)

def run(conn, name):
    s, subj, b = parse_email((SAMPLES / name).read_text())
    return process_rfq(conn, s, b, subj, force_rules=True)

# ---- pricing: hand-computed -------------------------------------------------
def test_cif_price_matches_hand_calculation(conn):
    ref = load_reference(conn)
    r = price_quote([("LED-PNL-60", 300), ("LED-BULB-9W", 1000)], ref["catalog"], ref["suppliers"],
                    ref["fx"], ref["freight"], "CIF", "HOUSTON", 0.18)
    # goods 4865.00 (CNY->USD @0.139) + freight 350 (LCL minimum; 1.65 CBM x 128 = 211.20) + insurance 24.325
    assert float(r["components"]["goods"]) == 4865.00
    assert float(r["components"]["freight"]) == 350.00
    assert float(r["price_total"]) == 6389.42           # 5239.325 / 0.82
    assert sum(l["line_total"] for l in r["lines"]) == r["price_total"]   # lines always sum to the total

def test_fob_excludes_freight_insurance_duty(conn):
    ref = load_reference(conn)
    r = price_quote([("SOL-INV-5K", 10)], ref["catalog"], ref["suppliers"], ref["fx"], ref["freight"], "FOB", "HOUSTON", 0.12)
    assert float(r["components"]["freight"]) == 0 and float(r["components"]["duty"]) == 0
    assert float(r["price_total"]) == round(6120 / 0.88, 2)

def test_ddp_adds_duty_and_clearing(conn):
    ref = load_reference(conn)
    r = price_quote([("SOL-BAT-10K", 5)], ref["catalog"], ref["suppliers"], ref["fx"], ref["freight"], "DDP", "SAVANNAH", 0.12)
    assert float(r["components"]["duty"]) == round(7400 * 0.034, 2)
    assert float(r["components"]["clearing"]) == 185.00

# ---- extraction -------------------------------------------------------------
def test_rules_extract_quantities_with_commas_and_formats():
    r = extract_rules("Quote 1,000 pcs LED-BULB-9W and 20 x SOL-INV-5K, also PKG-CTN-L x 5,000. CIF Houston")
    assert [(l.sku, l.qty) for l in r.lines] == [("LED-BULB-9W", 1000), ("SOL-INV-5K", 20), ("PKG-CTN-L", 5000)]
    assert r.incoterm == "CIF" and r.destination == "HOUSTON"

def test_llm_json_is_normalised_and_never_guesses_quantity():
    r = _normalize_llm_json('```json\n{"lines":[{"sku":"led-pnl-60","qty":null},{"sku":null,"qty":5}],"incoterm":"cif","destination":"Port of Savannah","confidence":0.8}\n```')
    assert r.lines[0].sku == "LED-PNL-60" and r.lines[0].qty is None
    assert r.incoterm == "CIF" and r.destination == "SAVANNAH"

# ---- workflow routing & controls -------------------------------------------
@pytest.mark.parametrize("file,status,flag", [
    ("rfq_01_clean_cif.txt", "DRAFT", ""), ("rfq_08_clean_fob.txt", "DRAFT", ""),
    ("rfq_02_high_value_ddp.txt", "NEEDS_REVIEW", "HIGH_VALUE"), ("rfq_03_below_moq.txt", "NEEDS_REVIEW", "BELOW_MOQ"),
    ("rfq_04_unknown_sku.txt", "NEEDS_INFO", "UNKNOWN_SKU"), ("rfq_05_missing_qty.txt", "NEEDS_INFO", "MISSING_QTY"),
    ("rfq_06_unknown_customer.txt", "NEEDS_INFO", "UNKNOWN_CUSTOMER"), ("rfq_07_freetext_no_sku.txt", "NEEDS_INFO", "NO_LINES_FOUND")])
def test_sample_rfqs_route_correctly(conn, file, status, flag):
    q, _ = get_quote(conn, run(conn, file))
    assert q["status"] == status and flag in q["flags"]

def test_high_value_needs_manager(conn):
    qid = run(conn, "rfq_02_high_value_ddp.txt")
    with pytest.raises(PermissionError): approve_quote(conn, qid, "ann", "reviewer")
    approve_quote(conn, qid, "bob", "manager")
    assert get_quote(conn, qid)[0]["status"] == "SENT"
    assert conn.execute("SELECT COUNT(*) FROM outbox WHERE quote_id=?", (qid,)).fetchone()[0] == 1

def test_nothing_is_sent_without_approval_and_needs_info_cannot_be_approved(conn):
    ok, bad = run(conn, "rfq_01_clean_cif.txt"), run(conn, "rfq_05_missing_qty.txt")
    assert conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    with pytest.raises(ValueError): approve_quote(conn, bad, "ann", "reviewer")
    request_info(conn, bad, "ann")
    assert get_quote(conn, bad)[0]["status"] == "AWAITING_CUSTOMER"
    assert "PMP-SUB-1HP" in conn.execute("SELECT body FROM outbox WHERE quote_id=?", (bad,)).fetchone()[0]

def test_audit_trail_records_every_step(conn):
    qid = run(conn, "rfq_03_below_moq.txt"); approve_quote(conn, qid, "ann", "reviewer")
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE quote_id=? ORDER BY id", (qid,))]
    assert actions == ["RFQ_RECEIVED", "EXTRACTED", "PRICED", "APPROVED_AND_SENT"]
