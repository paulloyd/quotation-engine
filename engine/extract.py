"""RFQ email -> structured fields.
Two paths: (1) LLM (Anthropic API) when ANTHROPIC_API_KEY is set; (2) deterministic rules.
The LLM output is NEVER trusted: the pipeline validates every SKU/qty against the catalog."""
import os, re, json
from dataclasses import dataclass, field

SKU_RE = re.compile(r"\b[A-Z]{2,4}-[A-Z0-9]{2,6}(?:-[A-Z0-9]{1,6})?\b")
NUM = r"(\d{1,3}(?:,\d{3})+|\d+)"          # 5,000 or 5000 (never swallows a trailing comma)
BEFORE_QTY = re.compile(NUM + r"\s*(?:units?|pcs|pieces|x|×)?(?:\s+of)?\s*$", re.I)
AFTER_QTY = re.compile(r"^\s*(?:(?:x|×|:|-)\s*" + NUM + r"|\(\s*" + NUM + r"\s*(?:units?|pcs)?\s*\))", re.I)
PORTS = {"houston": "HOUSTON", "savannah": "SAVANNAH", "long beach": "LONG BEACH", "los angeles": "LONG BEACH"}

@dataclass
class ExtractedLine:
    sku: str | None
    qty: int | None

@dataclass
class ExtractedRFQ:
    lines: list = field(default_factory=list)
    incoterm: str | None = None
    destination: str | None = None
    confidence: float = 0.0
    method: str = "rules"
    notes: list = field(default_factory=list)

def _to_int(s): return int(s.replace(",", "")) if s else None

def extract_rules(body: str) -> ExtractedRFQ:
    rfq = ExtractedRFQ(method="rules")
    prev_end, merged = 0, {}
    matches = list(SKU_RE.finditer(body))
    for i, m in enumerate(matches):
        before = body[max(prev_end, m.start() - 40):m.start()].rstrip()
        after = body[m.end():m.end() + 25]
        qty = None
        b = BEFORE_QTY.search(before)
        if b: qty = _to_int(b.group(1))
        else:
            a = AFTER_QTY.match(after)
            if a: qty = _to_int(a.group(1) or a.group(2))
        prev_end = m.end()
        if m.group() in merged and qty is not None and merged[m.group()] is not None:
            merged[m.group()] += qty
        else:
            merged[m.group()] = qty
    rfq.lines = [ExtractedLine(s, q) for s, q in merged.items()]
    t = re.search(r"\b(FOB|CIF|DDP)\b", body)
    rfq.incoterm = t.group(1) if t else None
    low = body.lower()
    for key, port in PORTS.items():
        if key in low: rfq.destination = port; break
    conf = 0.9
    if not rfq.lines: conf -= 0.5; rfq.notes.append("No SKU codes found (free-text request?)")
    if any(l.qty is None for l in rfq.lines): conf -= 0.25
    rfq.confidence = round(max(conf, 0.05), 2)
    return rfq

LLM_SYSTEM = ("You extract request-for-quotation data from customer emails for an importer. "
  "Return ONLY a JSON object: {\"lines\":[{\"sku\":string|null,\"qty\":integer|null}],"
  "\"incoterm\":\"FOB\"|\"CIF\"|\"DDP\"|null,\"destination\":string|null,\"confidence\":number 0-1}. "
  "Use ONLY SKUs from the provided catalog; if a product is ambiguous or not in the catalog set sku to null. "
  "Never guess quantities: if no quantity is stated use null. No commentary.")

def _normalize_llm_json(text: str) -> ExtractedRFQ:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    data = json.loads(text)
    rfq = ExtractedRFQ(method="llm")
    for l in data.get("lines", []):
        sku = (l.get("sku") or None); sku = sku.upper() if sku else None
        qty = l.get("qty"); qty = int(qty) if isinstance(qty, (int, float)) and qty > 0 else None
        rfq.lines.append(ExtractedLine(sku, qty))
    inc = (data.get("incoterm") or "").upper()
    rfq.incoterm = inc if inc in ("FOB", "CIF", "DDP") else None
    dest = (data.get("destination") or "").lower()
    rfq.destination = next((p for k, p in PORTS.items() if k in dest), None)
    rfq.confidence = float(data.get("confidence", 0.5))
    return rfq

def extract_llm(body: str, catalog: dict) -> ExtractedRFQ:
    import anthropic
    client = anthropic.Anthropic()
    cat = "\n".join(f"{s}: {p['name']}" for s, p in catalog.items())
    msg = client.messages.create(model=os.getenv("QUOTE_LLM_MODEL", "claude-sonnet-4-6"), max_tokens=800,
        system=LLM_SYSTEM, messages=[{"role": "user", "content": f"CATALOG:\n{cat}\n\nEMAIL:\n{body}"}])
    return _normalize_llm_json(msg.content[0].text)

def extract(body: str, catalog: dict, force_rules: bool = False) -> ExtractedRFQ:
    if not force_rules and os.getenv("ANTHROPIC_API_KEY"):
        try:
            return extract_llm(body, catalog)
        except Exception as e:                      # any API/parse failure -> safe deterministic fallback
            rfq = extract_rules(body); rfq.notes.append(f"LLM failed ({type(e).__name__}); used rules")
            return rfq
    return extract_rules(body)
