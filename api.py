"""Thin HTTP layer so n8n (or any tool) can call the engine.   uvicorn api:app --port 8000"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from engine.db import init_db
from engine.pipeline import process_rfq, get_quote, approve_quote

app = FastAPI(title="Quotation Engine")
conn = init_db()

class RFQ(BaseModel):
    sender: str
    subject: str = ""
    body: str

class Approval(BaseModel):
    approver: str
    role: str = "reviewer"

@app.post("/rfq")
def create_quote(rfq: RFQ):
    qid = process_rfq(conn, rfq.sender, rfq.body, rfq.subject)
    q, lines = get_quote(conn, qid)
    return {"quote_id": qid, "status": q["status"], "flags": [f for f in q["flags"].split(",") if f],
            "total_usd": q["total_usd"], "customer_known": q["customer_id"] is not None, "lines": len(lines)}

@app.get("/quotes/{qid}")
def read_quote(qid: int):
    try: q, lines = get_quote(conn, qid)
    except TypeError: raise HTTPException(404, "quote not found")
    q.pop("raw_email", None); return {"quote": q, "lines": lines}

@app.post("/quotes/{qid}/approve")
def approve(qid: int, a: Approval):
    try: approve_quote(conn, qid, a.approver, a.role)
    except PermissionError as e: raise HTTPException(403, str(e))
    except ValueError as e: raise HTTPException(409, str(e))
    return {"quote_id": qid, "status": "SENT"}
