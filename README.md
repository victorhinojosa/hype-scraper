# hype-scraper

A scheduled Python service that scrapes structured event sources for **San Luis
Potosí** and writes them into the [Hype](../hype) Supabase database as
`status='draft'` events. You review and approve them in the admin panel — the
scraper never publishes.

It is **not** called by Vercel. The only integration point is the database (plus
the shared `event-covers` storage bucket). Runs on Render on a cron schedule.

## What it does, per run

For each source (Passline, Arema, Solcet):

1. **Scrape** the source's SLP listing and per-event structured data.
2. **Dedup** against the `scraped_events` log — skip anything we've seen before,
   *even if you later deleted/archived/rejected that event*. Nothing reappears.
3. **Match the venue** against your saved venues (stripping codes/prefixes, same
   idea as the flyer autofill). On match: set `venue_id` and inherit
   neighborhood/address/maps from the venue. Otherwise leave `venue_id` null with
   `venue_name` filled.
4. **Re-host the flyer** into the `event-covers` bucket and store the public URL
   in `cover_image_url`.
5. **AI fallback** (Anthropic) *only* for fields the structured data was missing
   *and* only when a flyer image is available. For Passline this rarely fires.
6. **Insert** the draft and **log** it to `scraped_events`.

`category_id`, tags, and `description` are intentionally left empty — you set
those manually on review (`description` is stored as `''`).

## Architecture note — AI fallback is decoupled

The AI fallback calls the Anthropic SDK **directly from Python** (see
`hype_scraper/ai_fallback.py`), rather than POSTing to the web app's
`/api/flyer-extract` route. Rationale: the scraper's whole design is to be
independent of Vercel, so it must not depend on the web app being deployed and
awake. The prompt is a copy of `hype/src/lib/prompts/flyer-extract.ts`; if you
change one, consider the other, but they're allowed to diverge.

## How Passline is scraped (no proxy needed)

Passline's public *pages* are behind Cloudflare (the listing page uses a full JS
challenge), but its **backend JSON API is not**:

    POST https://api.passline.com/v1/event/GetBillboardByFilters

This is the same endpoint the site's own JS calls. We hit it directly with
`curl_cffi` (Chrome impersonation) — no proxy, no headless browser, no ScraperAPI.
It returns a clean JSON array of SLP events with slug, name, venue, start/end
date+time, price, flyer image, and ticket URL.

The one field the API omits is the venue **street address**. For that we fetch
the detail page (`www.passline.com/eventos/<slug>`, which is *not* JS-challenged)
and read `streetAddress` from its schema.org JSON-LD — but only for **new** events
(after dedup), so it's cheap. If it fails, the draft is still created (the matched
venue or the admin supplies the address).

> We originally tried ScraperAPI/FlareSolverr to solve the listing challenge;
> finding the JSON API made all of that unnecessary. If Passline ever locks the
> API down, `curl_cffi` + the detail JSON-LD is the fallback, or a JS-rendering
> proxy for discovery.

## The other two sources

**Arema** (`arema.mx`) — a React SPA with an open JSON API. One call,
`POST https://t3lb.arema.mx/public/events/list`, returns every national event;
we keep those whose `city` is San Luis Potosí (~18). Each record's `date` is a
unix timestamp that includes the local show time. Flyer is at a predictable CDN
path; ticket URL is `arema.mx/e/{id}`. No AI needed (price is left null).

**Solcet** (`solcet.mx`) — an SLP-focused, server-rendered site. We parse the
homepage cards (slug, name, `venue · city`, price) and keep the SLP-city ones,
then read each event page's schema.org JSON-LD for the venue + flyer image. The
date/time aren't in structured data (only on the flyer), so the **AI fallback**
reads them off the flyer. Events whose flyer yields no date are logged but not
inserted (no dateless drafts).

**Geo scope:** all three filter on the **city** of San Luis Potosí, not the
state — events in Xilitla/Matehuala/etc. are excluded.

**AI usage:** the fallback only fires when an *essential* field (name, venue,
date, time) is missing AND a flyer exists — so Passline/Arema rarely call it;
Solcet calls it once per new event for the date/time. Missing price never
triggers it (price is optional, admin adds on review).

## Setup

### 1. Database migration (one-time)

Apply `hype/supabase/migrations/007_scraped_events_dedup.sql` in the Supabase SQL
editor. It creates the `scraped_events` dedup log.

### 2. Local

```bash
python -m venv .venv 
.\.venv\Scripts\Activate.ps1    # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env      # fill in the three secrets from hype/.env.local
DRY_RUN=1 python run.py   # parse + log everything, write nothing
python run.py             # real run
```

Env vars: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (service role, **not**
anon), `ANTHROPIC_API_KEY`. Optional: `DRY_RUN`, `SOURCES=passline`.

### 3. Render

Deploy `render.yaml` as a Blueprint (a Cron Job). Set the three secrets in the
Render dashboard (they're marked `sync: false`). Default schedule: twice daily at
09:00 & 21:00 America/Mexico_City.

## Adding a source

Implement `scrape() -> list[ScrapedEvent]` in `hype_scraper/sources/<name>.py`
and register it in `hype_scraper/sources/__init__.py`. The pipeline handles
everything after that. `arema.py` / `solcet.py` are ready-to-fill stubs.

Set a **stable** `source_event_id` per event (Passline uses the URL slug) — it's
the dedup key.

## Layout

```
run.py                      entry point + per-source failure isolation
render.yaml                 Render cron blueprint
hype_scraper/
  config.py                 env loading, DRY_RUN
  models.py                 ScrapedEvent (the shape sources produce)
  http.py                   Cloudflare-capable HTTP (curl_cffi)
  db.py                     Supabase: venues, scraped_events, event insert
  venues.py                 venue name matching
  images.py                 download flyer -> upload to event-covers
  ai_fallback.py            Anthropic fallback (ported flyer prompt)
  pipeline.py               dedup -> match -> image -> map -> insert -> log
  sources/
    passline.py             source #1 — JSON API + detail JSON-LD
    arema.py                source #2 — JSON API (t3lb)
    solcet.py               source #3 — HTML cards + JSON-LD + AI date/time
```
