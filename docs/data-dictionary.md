# Data dictionary — units and definitions

Ambiguous units are the #1 source of silent bugs. Conventions:

- **Fractions**: returns, margins, growth, drawdowns, weights, price-vs-MA are
  fractions (`0.042` = **4.2%**). Multiply by 100 only at display time.
- **USD**: prices, SMAs, ATR, Bollinger bands, fundamentals line items, market cap.
- **0–100**: RSI, breadth percentages (`pct_above_sma_*`).
- Nulls mean "source could not supply it" — never fabricated.

## Identity / context (metrics table)

| column | meaning |
|---|---|
| symbol, name | ticker + company name |
| sector | canonical GICS-like sector (S&P GICS preferred, Nasdaq screener mapped) |
| industry | finer Nasdaq-screener industry label (unnormalized; null for non-screener names) |
| market_cap | USD, from Nasdaq screener at universe build |
| in_nasdaq100 / in_sp500 / in_russell1000 / in_russell3000 | boolean membership. Russell = top-1000/3000 by market cap **proxy**, not official FTSE Russell |
| as_of | date of the latest price bar used |
| latest_close | latest raw close, USD |

## Technicals (on adjusted close unless noted)

| column | definition |
|---|---|
| sma_20/50/200 | simple moving averages, USD |
| price_vs_sma_* | close/SMA − 1, fraction |
| rsi_14 | Wilder RSI, 0–100 |
| macd, macd_signal, macd_histogram | 12/26 EMA, 9-period signal, USD |
| bollinger_upper/middle/lower, bollinger_percent_b | 20-day, 2σ (population) |
| atr_14 | 14-day average true range on raw OHLC, USD |
| volume, relative_volume_20 | shares; latest ÷ 20-day average |

## Returns & risk (adjusted close)

| column | definition |
|---|---|
| return_1d/5d/1m/3m/6m/ytd/1y | period simple returns, fractions |
| momentum_3m/6m/12m | period return **excluding the most recent month**, fraction |
| volatility_20d/60d/252d | annualized stdev of daily log returns (√252, ddof=1), fraction |
| beta_qqq/beta_spy, correlation_qqq/correlation_spy | vs ETF proxy, trailing 252 shared days |
| high_52w / low_52w (USD), drawdown_52w, max_drawdown | drawdowns are fractions ≤ 0 |

## Fundamentals (SEC EDGAR companyfacts, latest annual period)

Line items (USD): revenue, gross_profit, operating_income, net_income,
free_cash_flow, cash_and_equivalents, total_debt, net_debt, capex,
research_and_development, shares_outstanding.

Ratios (fractions): revenue_growth (YoY), gross/operating/net/fcf_margin, roe,
roic (simplified pre-tax), fcf_conversion (FCF/NI), capex_to_revenue,
rnd_to_revenue. `latest_filing_date` drives incremental refresh.

**Valuation (pe_ratio, ps_ratio, pb_ratio, ev_to_sales, fcf_yield) is currently
always null** — computing it (price × shares join) is roadmap item B6 in
`docs/ARCHITECTURE_AUDIT.md`. The UI shows "n/a"; never build features on these
until B6 lands.

## Filings

symbol, cik, form (10-K/10-Q/8-K/4), filed_date, accession_number, url
(EDGAR primary document/index link).

## News (`news` dataset, optional)

symbol (null = market-wide), headline, source, url, published_at (UTC),
summary. Finnhub free tier; top-N universe names by market cap get company
headlines (config `news_top_symbols`).

## Provenance columns (every stored dataset)

`_source`, `_fetched_at` (UTC), `_disclaimer`
("prototype / delayed / unofficial source"), `_notes`. The read API strips
them from tables and exposes them via `provenance()`.
