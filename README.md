# 6551 Twitter to Binance Square

This skill mirrors Twitter/X posts to Binance Square and can also query Binance
Square discussion heat for a specific token.

## New: Binance Square Token Heat

Run:

```bash
python3 scripts/auto_mirror.py --mode square_heat --token BTC --heat-window-hours 24
```

JSON output:

```bash
python3 scripts/auto_mirror.py --mode square_heat --token BTC --json
```

The report includes:

- fetched post count
- posts inside the selected time window
- unique authors
- likes, comments, shares, views
- weighted engagement
- 1-hour and 6-hour recent post velocity
- 0-100 heat score
- top posts by engagement

If Binance changes its public search endpoint, pass a replacement endpoint:

```bash
SQUARE_SEARCH_URL="https://www.binance.com/bapi/..." \
python3 scripts/auto_mirror.py --mode square_heat --token BNB
```

## Twitter/X Mirror

Required environment variables:

- `TWITTER_TOKEN`
- `SQUARE_API_KEY`

Dry run:

```bash
python3 scripts/auto_mirror.py --mode search --keywords "bitcoin ETF" --dry-run
```
