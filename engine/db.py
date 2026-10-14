import sqlite3, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "quotes.db"

def connect(path=None):
    conn = sqlite3.connect(str(path or DEFAULT_DB), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db(path=None, rebuild=False):
    """Create the SQLite DB from sql/schema.sql + sql/seed_data.sql if it doesn't exist yet."""
    p = pathlib.Path(path or DEFAULT_DB)
    if rebuild and p.exists():
        p.unlink()
    conn = connect(p)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='quotes'").fetchone():
        conn.executescript((ROOT / "sql" / "schema.sql").read_text())
        conn.executescript((ROOT / "sql" / "seed_data.sql").read_text())
        conn.commit()
    return conn

def load_reference(conn):
    """Everything the pure pricing/extraction code needs, as plain dicts."""
    suppliers = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM suppliers")}
    catalog = {r["sku"]: dict(r) for r in conn.execute("SELECT * FROM products")}
    fx = {r["currency"]: r["usd_per_unit"] for r in conn.execute("SELECT * FROM fx_rates")}
    freight = {(r["origin_port"], r["dest_port"]): (r["rate_per_cbm"], r["min_charge"])
               for r in conn.execute("SELECT * FROM freight_rates")}
    customers = {r["email_domain"]: dict(r) for r in conn.execute("SELECT * FROM customers")}
    return dict(suppliers=suppliers, catalog=catalog, fx=fx, freight=freight, customers=customers)
