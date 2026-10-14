PRAGMA foreign_keys = ON;

-- Reference data (what the quote engine prices against)
CREATE TABLE suppliers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, origin_port TEXT NOT NULL,
  currency TEXT NOT NULL CHECK (currency IN ('USD','CNY')), lead_time_days INTEGER NOT NULL);
CREATE TABLE products (
  sku TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  unit_cost REAL NOT NULL,              -- in the supplier's currency
  moq INTEGER NOT NULL, cbm_per_unit REAL NOT NULL,
  hs_code TEXT NOT NULL, duty_rate REAL NOT NULL);   -- illustrative rates, not a tariff schedule
CREATE TABLE freight_rates (
  origin_port TEXT NOT NULL, dest_port TEXT NOT NULL,
  rate_per_cbm REAL NOT NULL, min_charge REAL NOT NULL, PRIMARY KEY (origin_port, dest_port));
CREATE TABLE fx_rates (currency TEXT PRIMARY KEY, usd_per_unit REAL NOT NULL);
CREATE TABLE customers (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email_domain TEXT NOT NULL UNIQUE,
  tier TEXT NOT NULL CHECK (tier IN ('A','B','C')), margin_pct REAL NOT NULL, payment_terms TEXT NOT NULL);

-- Synthetic history of manually prepared quotes (baseline for the impact report)
CREATE TABLE quote_history (
  id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  created_at TEXT NOT NULL, line_count INTEGER NOT NULL, total_usd REAL NOT NULL,
  margin_pct REAL NOT NULL, prep_minutes REAL NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('WON','LOST')));

-- Workflow tables (written by the engine)
CREATE TABLE quotes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER REFERENCES customers(id),
  sender TEXT NOT NULL, subject TEXT, incoterm TEXT, destination TEXT,
  status TEXT NOT NULL, extraction_method TEXT, extraction_confidence REAL,
  created_at TEXT NOT NULL, total_usd REAL, margin_pct REAL, flags TEXT DEFAULT '',
  breakdown TEXT, raw_email TEXT, process_ms INTEGER, approved_by TEXT, approved_at TEXT);
CREATE TABLE quote_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id INTEGER NOT NULL REFERENCES quotes(id),
  sku TEXT, qty INTEGER, unit_price_usd REAL, line_total_usd REAL, cost_usd REAL, flags TEXT DEFAULT '');
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id INTEGER NOT NULL REFERENCES quotes(id),
  ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT);
CREATE TABLE outbox (   -- stands in for the email provider; nothing is ever really sent
  id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id INTEGER NOT NULL REFERENCES quotes(id),
  to_addr TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
