-- Business question: is quote prep time (the thing we're automating) getting worse as volume grows,
-- and what does that cost in analyst hours?  Uses a CTE + window functions (SQLite 3.25+ / Postgres).
WITH monthly AS (
  SELECT substr(created_at, 1, 7)                          AS month,
         COUNT(*)                                          AS quotes,
         ROUND(AVG(prep_minutes), 1)                       AS avg_prep_min,
         ROUND(SUM(prep_minutes) / 60.0, 1)                AS prep_hours,
         ROUND(100.0 * SUM(outcome = 'WON') / COUNT(*), 1) AS win_rate_pct,
         ROUND(AVG(margin_pct) * 100, 1)                   AS avg_margin_pct
  FROM quote_history
  GROUP BY 1
)
SELECT month, quotes, avg_prep_min, prep_hours, win_rate_pct, avg_margin_pct,
       ROUND(AVG(avg_prep_min) OVER (ORDER BY month ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 1) AS rolling3_prep_min,
       quotes - LAG(quotes) OVER (ORDER BY month)                                                  AS volume_change_mom,
       ROUND(SUM(prep_hours) OVER (ORDER BY month), 1)                                              AS cumulative_prep_hours
FROM monthly
ORDER BY month;

-- Where does the effort go?  Prep minutes by quote size, and who the biggest won accounts are (RANK per tier).
SELECT c.tier, c.name,
       COUNT(*)                                       AS quotes,
       ROUND(SUM(CASE WHEN h.outcome='WON' THEN h.total_usd ELSE 0 END), 0) AS won_revenue,
       ROUND(AVG(h.prep_minutes), 1)                  AS avg_prep_min,
       RANK() OVER (PARTITION BY c.tier ORDER BY SUM(CASE WHEN h.outcome='WON' THEN h.total_usd ELSE 0 END) DESC) AS rank_in_tier
FROM quote_history h JOIN customers c ON c.id = h.customer_id
GROUP BY c.id
ORDER BY c.tier, rank_in_tier;
