# Market Data Source Evaluation

Research date: **2026-10-24**. This evaluation is based on public provider
documentation and pricing pages only. **No live provider calls were made and no
credentials were used.** Claims marked "not verified" require confirmation with
a real account before they are relied on.

The platform must not be described as survivorship-bias-free until delisted
coverage and historical identifiers are actually verified for the chosen source.

## Requirements driving the choice

1. Daily OHLCV with split/dividend-adjusted closes for total-return labels.
2. Long history (25–30+ years) with explicit coverage limits.
3. Delisted/inactive securities and a permanent security identifier, so failed
   companies are not silently removed.
4. Point-in-time availability and corporate-action handling.
5. Affordable or free for initial research, with a clear license for internal
   research use and no redistribution.

## Summary

| Provider | Access / cost (as published) | History | Adjustments | Delisted / identifiers | Licensing | Status here |
|---|---|---|---|---|---|---|
| **Tiingo** | API token. Starter $0: 50 req/hr, 1,000/day, 500 unique symbols/month. Power $30/mo. | 30+ years advertised; product page cites data from 1962. | Raw OHLCV + `adjOpen/adjHigh/adjLow/adjClose/adjVolume`; `divCash`, `splitFactor`; CRSP-based split+dividend adjustment. | EOD delisted completeness is **not documented**. Metadata has `startDate`/`endDate`; `permaTicker` and an `isDelisted` flag appear in fundamentals, not clearly in EOD. Third-party reviews do not treat Tiingo EOD as survivorship-free. | Both plans marked “Internal Use Only”. No redistribution. | **Adapter implemented**, mocked tests + gated live smoke test. Use for initial integration and free-tier checks, not as a survivorship-free source. |
| **EODHD** | Token in query string. Free: 20 calls/day, 500 welcome calls, 1 year of history. Paid from about £19.99/mo. | 30+ years; examples back to 1927 (S&P 500) and 1962 (IBM). | Raw OHLCV + `adjusted_close` (split+dividend). Adjusted closes are recalculated when dividends occur. Splits/dividends APIs separate. | Explicit: “A delisted ticker keeps its history” (e.g. TWTR); separate delisted dataset. Renamed symbols are separate histories (FB.US empty; use META.US) and a symbol-change API exists. | Free tier is “personal use”; commercial/B2B terms separate. Redistribution requires review. | **Recommended first paid candidate** for delisted-aware work. Adapter not implemented yet. |
| **Massive (formerly Polygon.io)** | API key. Basic $0: 5 calls/min, 2 years. Starter $29/mo: 5 years. Developer $79: 10 years. Advanced $199: 20+ years. | Up to 20+ years at the top tier. | Daily aggregates support `adjusted=true` for split/dividend adjustments; splits and dividends endpoints are separate. | Ticker search has an `active` filter, but adjusted-price behavior for delisted symbols is **not documented** in the public pages reviewed. | Plans marked individual/non-professional; redistribution not specified. | Not selected. Requires verification of delisted adjusted history. |
| **Alpha Vantage** | API key. Adjusted daily endpoint is premium; raw daily is available to free keys (compact 100 points; full history premium). | 25+ years. | `TIME_SERIES_DAILY_ADJUSTED` returns raw OHLCV plus adjusted close and split/dividend events; no adjusted OHLC. | Delisted handling is **not addressed** in the documentation reviewed. | Commercial use requires sales contact; redistribution terms not stated. | Not selected. Free tier cannot provide long adjusted history. |
| **Sharadar (Nasdaq Data Link)** | Paid tables/API/bulk downloads; free tier covers only the 30 Dow companies’ fundamentals and market data. | “Active and delisted coverage extends back to the 90s.” | Public page does not document the field schema or adjustment methodology. | Active and removed companies; “nearly completely free of survivorship bias.” | Redistribution/licensing not described on the public page; must be reviewed. | **Strong candidate** for survivorship-free research after purchasing/reviewing the license. CSV adapter can ingest a licensed export. |
| **Norgate Data** | Subscription; 6- or 12-month terms; no auto-renewal; access ends when it lapses. Price not verified on the pages reviewed. | Depends on subscription level; exact start dates not published on the page reviewed. | Corporate actions are included, but public adjustment details are limited. | Marketed as survivorship-bias-free US equities; delisted securities are part of that claim (specific counts not published). | Subscription license; redistribution restricted. Windows-only database with Python integration; export limited to historical price data. | Candidate for survivorship-free research if Windows-only access and licensing are acceptable. |
| **CRSP** | Institutional license; price on request. | 100+ years of daily/monthly data; 36,000+ active and inactive securities. | Corporate actions included; detailed methodology not on the public page. | Explicitly includes inactive securities; permanent `PERMNO`/`PERMCO` identifiers. | Institutional license; no public pricing. | Gold-standard benchmark **if institutional access exists**. Not usable without a license. |

**Excluded without deep evaluation:** Yahoo Finance/`yfinance` (unofficial
endpoints, no service guarantee, incomplete delisted coverage), Stooq (free bulk
CSV but coverage, adjustment method, and licensing are unclear), and Kaggle or
other scraped mirrors (unverifiable provenance and licensing). SEC EDGAR is a
legitimate source for filings and fundamentals, not for prices.

## What is implemented

### CSV adapter (`--provider csv`)

Provider-neutral and fully offline. It ingests licensed bulk exports or
hand-curated files with the schema documented in the README. This is the safe
path for Sharadar, Norgate, CRSP, or vendor downloads, subject to their licenses.
`adj_close` is mandatory because labels must use total returns.

### Tiingo adapter (`--provider tiingo`)

Implemented against the public endpoint and field documentation:

- `GET https://api.tiingo.com/tiingo/daily/<ticker>` — metadata.
- `GET https://api.tiingo.com/tiingo/daily/<ticker>/prices` — daily prices with
  `startDate`, `endDate`, `format=json`.
- Authentication: `Authorization: Token <key>`.

The token is read from `TIINGO_API_KEY`, never stored in manifests or error
messages. Requests retry only on 429/5xx/network failures, with exponential
backoff; 401/403/404 fail immediately.

Offline tests cover the documented response schema, retries, error handling,
normalization, provenance, and idempotent re-runs. **They do not prove that the
live API matches the documentation or that a symbol's coverage is complete.**
With a token, run:

```bash
uv run python -m pytest -m integration
```

### Why EODHD was not implemented first

EODHD looks stronger on delisted coverage, but its free tier cannot test long
history and its token is passed in the query string (higher leak risk in logs).
The provider interface is small; adding an EODHD adapter later is a contained
change. Selection should follow a license and coverage check.

## Verify coverage before research use

1. Pick a list of active **and delisted** tickers known to have existed in the
   requested period.
2. Ingest them with a wide date range.
3. Run `coverage` and inspect `missing_weekdays`, first/last dates, and absent
   symbols.
4. Spot-check corporate actions and adjusted closes against a second source.
5. Record the exact provider, plan, request dates, and retrieval time in the raw
   manifests.
6. Do not call the result survivorship-bias-free unless delisted coverage and
   identifier history have been verified.

## Point-in-time caveats

- Provider adjustments are revised retroactively. Raw snapshots preserve the
  retrieved revision, but the normalized dataset stores the latest revision and
  does not reconstruct historical vintages.
- Daily bars do not yet carry an `available_at` timestamp. Phase 3 must apply
  market-close availability rules before a bar can be used at a forecast time.
- Index membership, exchange listing changes, and ticker reuse are not modeled.
  Current `security_id` values are provisional `provider:symbol` ids unless a
  vendor supplies a permanent id in `securities.csv`.

## Sources

Official/public pages consulted on 2026-10-24:

- Tiingo EOD docs: https://www.tiingo.com/documentation/end-of-day
- Tiingo API connection/auth: https://www.tiingo.com/documentation/general/connecting
- Tiingo pricing: https://www.tiingo.com/pricing
- EODHD historical data: https://eodhd.com/financial-apis/api-for-historical-data-and-volumes/
- EODHD pricing: https://eodhd.com/pricing
- Massive/Polygon pricing: https://massive.com/pricing
- Alpha Vantage docs: https://www.alphavantage.co/documentation/
- Sharadar: https://www.sharadar.com/
- Norgate Data: https://norgatedata.com/
- CRSP US Stock Databases: https://indexes.morningstar.com/research-data-products/crsp-us-stock-databases

Pricing, coverage, and terms change; re-check before purchasing or publishing
results.
