-- Enrich corporate actions with detail text, date labels, and document links.

ALTER TABLE public.mn_corporate_actions
    ADD COLUMN IF NOT EXISTS date_label TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS document_url TEXT NOT NULL DEFAULT '';

DROP VIEW IF EXISTS public.mn_ticker_corporate_actions;

CREATE VIEW public.mn_ticker_corporate_actions AS
SELECT
    id,
    ticker,
    event_type,
    event_date_raw AS event_date,
    date_label,
    details,
    document_url,
    first_seen_at,
    last_seen_at
FROM public.mn_corporate_actions
ORDER BY ticker, event_date_raw DESC;

GRANT SELECT ON public.mn_ticker_corporate_actions TO anon, authenticated;

NOTIFY pgrst, 'reload schema';
