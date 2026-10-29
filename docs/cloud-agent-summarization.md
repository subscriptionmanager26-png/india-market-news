# Cloud Agent summarisation (replaces Edge `summarize-news` LLM)

## Relationship to **Pocketedge news desk brief** (Pramaan)

You already run a **separate** automation (often named *Pocketedge news desk brief*) on the **Pramaan** repo. That agent:

- Runs every **6 hours** (`0 */6 * * *` UTC)
- **Reads** `mn_news_items` **and** `mn_news_ai_summaries` (plus `af_*` Twitter/Substack) as **inspiration**
- **Writes** 0–5 **desk notes** to `src/lib/desk-notes.ts` and pushes **`main`** for https://pramaan.pocketedge.in/news

It does **not** backfill **`mn_news_ai_summaries`** for the full market-news firehose (~hundreds of tickers/day) or PocketEdge’s `ingest-news-summary` webhook on INSERT.

| | **Summarize automation** (this repo) | **News desk brief** (Pramaan) |
|--|--------------------------------------|--------------------------------|
| Repo | `india-market-news` | Pramaan (Next.js) |
| Output | Rows in **`mn_news_ai_summaries`** | **`/news`** desk notes + heroes |
| Volume | ~**40–80 stories/run**, several runs/day | **0–5** notes / 6h |
| Schedule | e.g. 4× weekday (keep **ahead of** desk) | `0 */6 * * *` UTC |

**Do not fold bulk summarisation into the desk prompt** — one run cannot reliably produce hundreds of per-ticker bullets *and* ship curated desk notes.

**Do reuse the same Supabase secrets** on both Cloud environments (`SUPABASE_URL` + service role for project `imrcllmpldvjoyjyluhr`).

**Optional desk tweak:** if a headline has no AI row yet, the desk can still use `mn_news_items.summary`; once this automation catches up, prefer **`ai_bullets`** from `mn_news_ai_summaries` for grounding.

---

## Quick checklist (you)

1. **Merge** the PR that adds `india-market-news-summarize` (this repo).
2. **Cursor → Cloud Agents → Environments** → link `india-market-news` → add runtime secrets (section 1 below).
3. **Trigger an environment build** (or wait for the next agent run) so `pip install -e .` from `.cursor/environment.json` succeeds.
4. **Cursor → Automations → New** → repo + environment → schedule → paste prompt from [`.cursor/automation/summarize-news-agent.md`](../.cursor/automation/summarize-news-agent.md).
5. **Disable** legacy Edge `summarize-news` LLM schedule in Supabase.
6. **Run automation once manually**; confirm new rows in `mn_news_ai_summaries` with `model` = `cursor:composer-2.5`.

---

TradingView ingest still writes **`mn_news_items`**. This workflow uses a **Cursor Cloud Agent** (Composer) to write **`mn_news_ai_summaries`**, which PocketEdge already reads.

Disable or ignore the Supabase Edge Function **`summarize-news`** LLM path (OpenAI/Mistral keys are exhausted). Ingest + PocketEdge `ingest-news-summary` on INSERT are unchanged.

In Supabase (Dashboard → Edge Functions → `summarize-news`, or `mn_job_config` cron/webhook): turn off the scheduled LLM invocations so you are not double-processing or logging failed OpenAI/Mistral calls. TradingView ingest is unrelated.

## 1. Cloud environment secrets

In **Cursor → Cloud Agents → Environments → india-market-news** (or your linked environment), set **runtime secrets**:

| Secret | Value |
|--------|--------|
| `SUPABASE_URL` | `https://imrcllmpldvjoyjyluhr.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Service role key from Supabase → Settings → API |

If you already use a secret named `service_role`, that also works (the CLI accepts either name).

Optional:

| Secret | Purpose |
|--------|---------|
| `SUMMARIZE_MODEL` | Stored in DB `model` column (default `cursor:composer-2.5`) |

Repository bootstrap: `.cursor/environment.json` runs `pip install -e .` on build.

## 2. Create a Cursor Automation

**Cursor → Automations → New automation**

- **Repository:** `india-market-news`
- **Environment:** the environment above (with secrets)
- **Schedule (IST, market days):** e.g. `0 9,12,15,18 * * 1-5` for 9:00, 12:00, 15:00, 18:00 IST Mon–Fri  
  (Adjust in the UI; 4 runs/day matches ~240 stories/day with batch size 40–60.)

Copy the **agent instructions** from [`.cursor/automation/summarize-news-agent.md`](../.cursor/automation/summarize-news-agent.md) into the automation prompt.

Recommended model: **Composer 2.5** (medium effort). Use **one automation run per batch**, not one agent per headline.

## 3. Manual test (same as the automation)

On a cloud agent (or locally with secrets):

```bash
pip install -e .
export PATH="$HOME/.local/bin:$PATH"

# 1) Pending queue (JSON)
india-market-news-summarize list-pending --limit 5 > pending.json

# 2) Agent writes summaries.json — see agent prompt format

# 3) Upsert to Supabase
india-market-news-summarize apply --input summaries.json
```

Verify in Supabase: new rows in **`mn_news_ai_summaries`** with `model` like `cursor:composer-2.5`.

## 4. Backlog

After the LLM outage (~Oct 2026), there is a large backlog. Run the automation with a higher limit for a few days, or temporarily set in the prompt:

- `list-pending --limit 80` per run  
- Extra run at **21:00 IST** until backlog clears  

Do **not** spawn parallel automations on the same queue without sharding (wasted tokens).

## 5. Cost control

- **Batch size:** 40 stories/run × 4 runs/day ≈ healthy steady state  
- **One automation** per schedule slot  
- Monitor **Cursor → Usage** weekly pool  

## 6. CLI reference

```bash
india-market-news-summarize list-pending [--limit 40] [--lookback-days 14]
india-market-news-summarize apply --input summaries.json [--model cursor:composer-2.5]
```
