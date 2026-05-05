# 6551 Twitter to Binance Square

Use this skill to mirror Twitter/X posts to Binance Square, or to inspect Binance
Square discussion heat for a token.

## Modes

### Mirror Twitter/X to Binance Square

Required environment variables:

- `TWITTER_TOKEN`: 6551/OpenTwitter API token.
- `SQUARE_API_KEY`: Binance Square OpenAPI key for publishing.

Examples:

```bash
python3 scripts/auto_mirror.py --mode search --keywords "bitcoin ETF" --dry-run
python3 scripts/auto_mirror.py --mode account --accounts "binance,cz_binance"
python3 scripts/auto_mirror.py --mode hashtag --hashtag bitcoin
```

### Query Binance Square token heat

This mode is read-only. It searches Binance Square for a token symbol, parses the
returned posts, and reports discussion heat.

Examples:

```bash
python3 scripts/auto_mirror.py --mode square_heat --token BTC --heat-window-hours 24
python3 scripts/auto_mirror.py --mode square_heat --token "$BNB" --square-max-results 100 --json
python3 scripts/auto_mirror.py --config config.square_heat.json
```

Optional environment variables:

- `SQUARE_SEARCH_URL`: comma-separated Binance Square search endpoints. Use this
  when Binance changes its public Web/BAPI search endpoint.
- `SQUARE_API_KEY`: optional for read-only search if your endpoint requires it.

## Heat Score

The heat score is a 0-100 composite metric based on:

- post count in the selected time window
- unique author count
- weighted engagement: likes + comments * 2 + shares * 3
- view count
- short-term momentum, measured by recent 6-hour post velocity

The output includes raw component metrics so downstream tools can apply their own
thresholds instead of relying only on the final score.
