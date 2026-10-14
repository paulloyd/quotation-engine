# Setup (about 20 minutes, free tools only)

Do these in order. Steps marked **[SCREENSHOT]** are the shots to put in your portfolio / video.

1. **Install**
   ```bash
   python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. **Prove it works:** `python -m pytest -q`  (expect 16 passed). **[SCREENSHOT]** the green run.
3. **(Optional) regenerate data:** `python scripts/generate_seed.py`
4. **Start the review console:** `streamlit run app.py`
   - Click **Load 8 sample RFQs** in the sidebar. **[SCREENSHOT]** the queue showing all five statuses.
   - Open quote #2 (high value): try **Approve & send** as `reviewer` (blocked), then switch Role to `manager`. **[SCREENSHOT]** the block and the success.
   - Open an unknown-SKU quote and click **Request info from customer**. **[SCREENSHOT]** the audit trail.
   - Open the **Impact** tab. State clearly that it is an estimate.
5. **(Optional) LLM mode:** `export ANTHROPIC_API_KEY=...` then reload the app and process quote 07 (free-text). Compare the `method` column (`llm` vs `rules`). Optional: `export QUOTE_LLM_MODEL=<model name>`. Check results yourself; this path is not covered by automated tests.
6. **Run the API:** `uvicorn api:app --port 8000`, then test:
   ```bash
   curl -X POST localhost:8000/rfq -H "Content-Type: application/json" \
     -d '{"sender":"Dana <dana@acmelighting.example>","subject":"RFQ","body":"Please quote 300 units of LED-PNL-60, CIF Houston"}'
   ```
7. **Run n8n:** `npx n8n` then open http://localhost:5678.
   - Workflows, Import from file, choose `workflow/n8n_quotation_workflow.json`.
   - Add IMAP and SMTP credentials (a throwaway Gmail with an app password, or Mailtrap for SMTP).
   - No inbox handy? Swap the first node for a **Manual Trigger** + **Edit Fields** node that sets `from`, `subject`, `textPlain`, and run it. **[SCREENSHOT]** the green execution with the branch taken.
8. **SQL proof:** run `sql/reporting_query.sql` in any SQLite client (e.g. `sqlite3 quotes.db < sql/reporting_query.sql`). **[SCREENSHOT]** the monthly trend.

## Video checklist (3-4 min)
Lead with the problem and the result you can truthfully state, then demo steps 4 and 7 in that order. Say plainly what is synthetic and what is mocked.
