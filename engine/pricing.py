"""Deterministic landed-cost pricing. NO LLM IN THIS FILE, on purpose: money math must be
reproducible, testable, and auditable. The LLM only reads emails and drafts text elsewhere."""
from decimal import Decimal as D, ROUND_HALF_UP

INCOTERMS = ("FOB", "CIF", "DDP")
INSURANCE_RATE = D("0.005")      # 0.5% of goods value (CIF/DDP)
CLEARING_FEE = D("185.00")       # flat customs-clearance fee (DDP only)

def d(x):  return D(str(x))
def cents(x): return D(x).quantize(D("0.01"), ROUND_HALF_UP)

def price_quote(lines, catalog, suppliers, fx, freight, incoterm, dest, margin):
    """lines: [(sku, qty)] with every sku in catalog and qty > 0.
    Cost build-up by Incoterm:  FOB = goods | CIF = + freight + insurance | DDP = + duty + clearing.
    Sell price = cost / (1 - margin)  (margin is a share of the selling price).
    Freight is charged per origin port (LCL: max(min_charge, rate x CBM)) and allocated by CBM share."""
    if incoterm not in INCOTERMS:
        raise ValueError(f"unsupported incoterm {incoterm}")
    rows = []
    for sku, qty in lines:
        p = catalog[sku]; s = suppliers[p["supplier_id"]]
        goods = d(p["unit_cost"]) * d(fx[s["currency"]]) * qty
        rows.append(dict(sku=sku, qty=qty, origin=s["origin_port"], goods=goods,
                         cbm=d(p["cbm_per_unit"]) * qty, duty_rate=d(p["duty_rate"]),
                         lead=s["lead_time_days"]))
    # freight per origin port, allocated by CBM
    port_cbm = {}
    for r in rows: port_cbm[r["origin"]] = port_cbm.get(r["origin"], D(0)) + r["cbm"]
    port_charge = {}
    for port, cbm in port_cbm.items():
        rate, minimum = freight[(port, dest)]      # KeyError -> caller flags UNSUPPORTED_LANE
        port_charge[port] = max(d(minimum), d(rate) * cbm)
    goods_total = sum((r["goods"] for r in rows), D(0))
    out_lines, tot = [], dict(goods=D(0), freight=D(0), insurance=D(0), duty=D(0), clearing=D(0))
    for r in rows:
        freight_l = port_charge[r["origin"]] * r["cbm"] / port_cbm[r["origin"]] if incoterm != "FOB" else D(0)
        insur_l = r["goods"] * INSURANCE_RATE if incoterm != "FOB" else D(0)
        duty_l = r["goods"] * r["duty_rate"] if incoterm == "DDP" else D(0)
        clear_l = CLEARING_FEE * r["goods"] / goods_total if incoterm == "DDP" else D(0)
        cost_l = r["goods"] + freight_l + insur_l + duty_l + clear_l
        price_l = cents(cost_l / (D(1) - d(margin)))
        out_lines.append(dict(sku=r["sku"], qty=r["qty"], cost=cents(cost_l), line_total=price_l,
                              unit_price=(price_l / r["qty"]).quantize(D("0.0001"), ROUND_HALF_UP)))
        for k, v in zip(tot, (r["goods"], freight_l, insur_l, duty_l, clear_l)): tot[k] += v
    cost_total = sum((l["cost"] for l in out_lines), D(0))
    price_total = sum((l["line_total"] for l in out_lines), D(0))
    return dict(lines=out_lines, components={k: cents(v) for k, v in tot.items()},
                cost_total=cost_total, price_total=price_total,
                lead_time_days=max(r["lead"] for r in rows), incoterm=incoterm, destination=dest)
