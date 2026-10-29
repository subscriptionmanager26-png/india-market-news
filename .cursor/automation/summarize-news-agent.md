# Automation: Summarise pending market news → Supabase

You are maintaining **`mn_news_ai_summaries`** for PocketEdge. Do not call OpenAI or Mistral APIs directly; you are the LLM.

## Steps (every run)

1. Install is already done in the environment. From repo root, ensure CLI is on `PATH`:

   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   india-market-news-summarize list-pending --limit 40 > /tmp/pending.json
   ```

2. Read `/tmp/pending.json`. For each entry in `stories` (up to 40), write **`ai_bullets`**:
   - 2–5 Markdown bullet lines starting with `- `
   - Wrap company names and tickers in `*bold*`
   - Wrap numbers, dates, and percentages in `*bold*`
   - Plain English for Indian retail investors; no investment advice disclaimers unless factual
   - Use `title` + `body_markdown` as source; do not invent facts

3. Write **`/tmp/summaries.json`** in this exact shape:

   ```json
   {
     "summaries": [
       {
         "source_external_id": "<from pending>",
         "title": "<headline>",
         "tickers": ["RELIANCE", "TCS"],
         "ai_bullets": "- *Company* did ...\\n- Revenue *12%* ...",
         "new_info": true
       }
     ]
   }
   ```

   - **`tickers`:** use the full `tickers` list from pending (all symbols for that story), not only `missing_tickers`.
   - Skip stories with empty `body_markdown` only if there is truly no content; otherwise summarise from title.

4. Apply to Supabase:

   ```bash
   india-market-news-summarize apply --input /tmp/summaries.json
   ```

5. Reply with a short summary: pending count, how many summaries written, and the JSON result of `apply`.

## Rules

- Do not modify Zerodha fetch workflows or Edge functions in this run.
- Do not commit secrets.
- If `list-pending` returns `"count": 0`, say the queue is empty and exit successfully.
- If `apply` errors, show the error and stop.
