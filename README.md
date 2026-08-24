# Wallapop Deal Notifier

Daily bot that searches [Wallapop](https://es.wallapop.com) for good prices on
specific products (default: houseplants) near Madrid and emails you when a **new**
bargain appears. Zero third-party dependencies — pure Python standard library.

## How a "good price" is decided

A listing is flagged if it is **not reserved** and meets at least one of:

1. price ≤ the search's configured `max_price`, or
2. price ≤ **40% below the market median** for that search term.

The market median is computed from all results for the term (before the distance
filter), so relative-bargain detection has enough samples. Distance only decides
which flagged listings get reported.

Each listing is reported **once** — `seen.json` tracks what's already been sent.

## Files

| File | Purpose |
|------|---------|
| `wallapop_deals.py` | Core finder + Gmail email digest |
| `server.py` | Local web UI (`http://localhost:8765`) to manage the watch-list |
| `config.json` | Location, radius, per-search max prices, good-price rule |
| `seen.json` | Dedupe memory (committed back each run) |
| `deals.json` | Latest results (also feeds the web UI) |

## Local use

```bash
python wallapop_deals.py     # run once, print + email if configured
python server.py             # open http://localhost:8765 to manage searches
```

## Automated daily run (GitHub Actions)

`.github/workflows/daily.yml` runs every day at **13:00 UTC (15:00 Spain)** and
commits updated state back to the repo.

### Required repository secrets

| Secret | Value |
|--------|-------|
| `GMAIL_USER` | Your Gmail address (sender) |
| `GMAIL_APP_PASSWORD` | A Gmail [app password](https://myaccount.google.com/apppasswords) |
| `MAIL_TO` | *(optional)* recipient; defaults to `GMAIL_USER` |

### Test the email

Actions → **Wallapop daily deals** → **Run workflow** → set `test_email` to
`true`. This emails the current deals so you can confirm delivery without waiting
for a genuinely new listing.
