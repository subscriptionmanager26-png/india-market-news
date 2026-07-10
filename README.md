# India Market News

NSE equity news and corporate actions from [Zerodha Markets](https://zerodha.com/markets/stocks/), stored in **Supabase** for UI consumption. Fetched automatically twice daily at **8 AM and 8 PM IST** via GitHub Actions.

## Architecture

```
GitHub repo (code + ticker list)
        │
        ▼
GitHub Actions (8 AM / 8 PM IST)
        │
        ▼
Zerodha Markets HTML scrape
        │
        ▼
Deduplication (content_hash)
        │
        ▼
Supabase `market_news` schema
        │
        ▼
Your UI (read via Supabase client)
```

**Data lives in Supabase, not GitHub.** GitHub only stores code, workflows, and the ticker CSV.

## Supabase

| Item | Value |
|------|-------|
| Project | `india-market-news` (`imrcllmpldvjoyjyluhr`, ap-northeast-1) |
| URL | `https://imrcllmpldvjoyjyluhr.supabase.co` |
| Schema | `public` (tables prefixed `mn_`) |

### Tables

| Table | Purpose |
|-------|---------|
| `mn_news_items` | Deduplicated news (90-day retention) |
| `mn_corporate_actions` | Dividends, results, bonus, etc. |
| `mn_tickers` | NSE symbol reference from `EQUITY_L.csv` |
| `mn_fetch_runs` | Job history and stats |

### UI views

- `mn_latest_news` — news from last 90 days, newest first
- `mn_ticker_corporate_actions` — corporate actions by ticker

### Deduplication

News: `SHA256(ticker + normalized_title + published_at)`  
Corporate actions: `SHA256(ticker + event_type + event_date)`

`summary` is stored as **Markdown** (bullets, paragraphs, bold) for UI rendering.

Duplicates are ignored on upsert via unique `content_hash` constraints.

## Fetch strategy

Zerodha rate-limits burst traffic. The fetcher uses **micro-batches**:

| Setting | Value |
|---------|-------|
| Parallel requests per micro-batch | 15 |
| Pause between micro-batches | 3 seconds |
| Supabase write batch size | 500 tickers |

~2,049 EQ tickers complete in **~15–18 minutes** on GitHub Actions.

Failed tickers are retried once at 8 parallel / 5s pause.

## Local setup

```bash
cd india-market-news
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Dry run (no Supabase)
india-market-news --dry-run --limit 100

# Full run (requires Supabase secrets)
export SUPABASE_URL="https://imrcllmpldvjoyjyluhr.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
india-market-news

# Or use the helper script (loads .env)
./scripts/run_fetch_local.sh
```

## GitHub Actions secrets

Add these in **Settings → Secrets → Actions**:

| Secret | Value |
|--------|-------|
| `SUPABASE_URL` | `https://imrcllmpldvjoyjyluhr.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | From Supabase Dashboard → Settings → API |

## Schedule

| IST | UTC (cron) |
|-----|------------|
| 8:00 AM | 02:30 |
| 8:00 PM | 14:30 |

Cron: `30 2,14 * * *`

Manual runs: **Actions → Fetch market news → Run workflow**

## Ticker list

`data/EQUITY_L.csv` — NSE equity list (~2,049 EQ series symbols).

Symbol mapping for Zerodha URLs:
- `BAJAJ-AUTO` → `BAJAJ_AUTO`
- `TATAMOTORS` → `TMPV`

## UI example (Supabase JS)

```javascript
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  'https://imrcllmpldvjoyjyluhr.supabase.co',
  'YOUR_ANON_KEY'
)

// Latest news for a ticker (summary is Markdown)
const { data } = await supabase
  .from('mn_latest_news')
  .select('*')
  .eq('ticker', 'RELIANCE')
  .limit(20)

// Render with react-markdown, marked, or similar
```

## License

MIT
