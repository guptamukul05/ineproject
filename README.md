# Price watch — mock store tracker

A small full-stack app that tracks products on
[demo.inelabteamdev.com](https://demo.inelabteamdev.com), scrapes their price and
stock every two hours, and keeps an honest record of every attempt — including
the ones that failed.

- **Live site:** _add your Vercel URL here_
- **API:** _add your Render URL here_
- **Headed run recording:** _add your video link here_

---

## What it does

1. Search the mock store by partial or full product name and start tracking a product.
2. A cron service calls the backend every 2 hours; the backend scrapes each due product.
3. Price and stock history is shown as a chart or a table.
4. A per-product scrape log lists every attempt with its timestamp, outcome and
   the full retry trace.

The interface leads with a **reliability ribbon**: one coloured tick per scrape
attempt, oldest on the left. Green is a clean read, amber succeeded only after a
retry, purple means a value was read but refused by validation, red means no
reading was taken. You can see the scraper's track record before you read a
single number.

---

## How the scraping works

The store is a JavaScript application. Fetching its HTML directly returns an
almost empty document — no price is present in the served markup. That single
fact drives the whole design.

### A ladder, not a single method

Each product is scraped by trying three strategies in order, and the one that
worked last time is tried first on the next run:

| Strategy | What it does | Cost |
|---|---|---|
| `json_api` | Calls the JSON endpoint the store's own frontend uses | one HTTP request |
| `http_html` | Fetches the page and parses HTML, including any state embedded in `<script>` tags | one HTTP request |
| `browser` | Renders the page in headless Chromium | ~2–5 s, ~300 MB RAM |

### The browser teaches the HTTP path

The first browser run listens to the page's own network traffic. Any JSON
response that looks like product data is recorded in the `SiteProfile` table.
From then on, the lightweight `json_api` strategy hits that endpoint directly
and the browser is never started.

This is how the app satisfies *"prefer lightweight HTTP fetching, reach for a
headless browser only where the page genuinely requires it"* against a site that,
on a cold read, appears to require a browser for everything. The browser is used
**once, to learn**, and then held in reserve as a fallback.

Run `python manage.py probe_store` to watch this discovery happen and see what
the scraper can find.

### Retries

Each strategy gets up to 4 attempts with exponential backoff (2s, 5s, 11s) plus
±40% jitter. Errors are classified: timeouts, connection failures and 5xx
responses are retried; a 404 or a page with no price is permanent and moves
straight to the next strategy rather than burning the retry budget.

### Nothing wrong ever gets stored

A reading has to pass `tracker/scraper/validators.py` before it reaches the
price history:

- The price must parse to a positive number in a plausible range.
- Ambiguous text returns `None` rather than a guess — if a blob mixes "MRP"
  with a live price, the parser refuses it instead of picking one.
- If the new price is more than 40% away from the rolling median of the last
  nine readings, one reading is not enough. The scraper **re-fetches using a
  different strategy**. If the second read agrees, the outlier is real and gets
  stored. If it disagrees, the attempt is logged as `rejected` and nothing is
  written to the history.

A failed or rejected scrape produces a log row and no price point. The chart can
have a gap; it cannot have a lie in it.

### Structure-change detection

After each successful scrape the scraper hashes a coarse description of the page
shape — which selectors matched, and the tag/class path of their ancestors. Price
changes do not move the hash; a redesign does. When it moves, the app raises a
`structure_change` alert rather than quietly reading the wrong element.

---

## Scraping schedule

Every **2 hours**, triggered externally by [cron-job.org](https://cron-job.org)
calling:

```
GET https://YOUR-BACKEND.onrender.com/api/cron/scrape/?key=YOUR_CRON_SECRET
```

Render's free tier sleeps after 15 minutes of inactivity, so there is no
always-on loop in the app. The cron call both wakes the instance and does the
work. Per-product frequency can be overridden in the UI (30 min to 1 day); the
cron job fires every 2 hours and the backend scrapes only the products that are
actually due.

---

## Environment variables

### Backend (`backend/.env`, or Render's environment settings)

| Variable | Required | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | yes | Any long random string |
| `DJANGO_DEBUG` | no | `false` in production |
| `DATABASE_URL` | yes | Supabase **session pooler** URI, port 5432 |
| `CRON_SECRET` | yes | Shared secret the cron job must send |
| `STORE_BASE_URL` | no | Defaults to the INE mock store |
| `SCRAPE_INTERVAL_MINUTES` | no | Defaults to `120` |
| `PLAYWRIGHT_ENABLED` | no | `false` disables the browser fallback |
| `CORS_ALLOWED_ORIGINS` | no | Your Vercel URL, comma-separated |
| `PLAYWRIGHT_BROWSERS_PATH` | on Render | `/opt/render/project/.playwright` |

### Frontend (`frontend/.env`, or Vercel's environment settings)

| Variable | Required | Notes |
|---|---|---|
| `VITE_API_BASE` | yes | Render backend URL, no trailing slash |

---

## Running it locally

```bash
# backend
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env               # then edit it
python manage.py migrate
python manage.py probe_store       # look at what the scraper can see
python manage.py runserver
```

```bash
# frontend, in a second terminal
cd frontend
npm install
cp .env.example .env
# set VITE_API_BASE=http://127.0.0.1:8000
npm run dev
```

Open http://localhost:5173.

### Useful commands

```bash
python manage.py probe_store           # inspect the store, save debug artefacts
python manage.py seed_demo             # track the first 4 products and scrape once
python manage.py scrape_now --force    # scrape everything, headless
python manage.py scrape_headed         # visible browser with an on-page HUD
python manage.py scrape_headed --chaos # ...plus injected slow and failing responses
python manage.py test tracker          # parser and validator tests
```

---

## The observable (headed) run

`python manage.py scrape_headed` opens a real Chromium window, slowed to 350 ms
per action, with a heads-up display drawn on the page. The HUD prints each
decision as it is made: navigating, waiting for the price to settle, which
attempt it is on, what it finally read.

`--chaos` adds a fault injector that randomly stalls requests for 3–6 seconds or
returns HTTP 503. The mock store is flaky on its own schedule, which is not much
use for a 3-minute recording; this makes the retry and recovery path reproducible
on demand. It is off by default and nothing in the scheduled path touches it.

---

## Deploying

Details and exact click-paths are in [`docs/DEPLOY.md`](docs/DEPLOY.md).

Short version: Supabase for the database, Render for the Django API (build
command `./build.sh`), Vercel for the React frontend (root directory
`frontend`), cron-job.org hitting `/api/cron/scrape/` every 2 hours.

---

## Layout

```
backend/
  config/                  Django settings, urls, wsgi
  tracker/
    models.py              Product, PricePoint, ScrapeAttempt, ScrapeRun, Alert, SiteProfile
    views.py               REST API
    tests.py               parser and validator tests
    scraper/
      engine.py            the ladder, retries, validation, persistence
      http_strategy.py     json_api and http_html strategies
      browser_strategy.py  Playwright, network learning, headed HUD
      parsing.py           price and stock text -> values
      validators.py        the gate before anything is stored
      fingerprint.py       structure-change detection
      chaos.py             fault injection for the recording only
      config.py            selectors, retry policy, thresholds
    management/commands/   probe_store, seed_demo, scrape_now, scrape_headed
frontend/
  src/App.jsx              dashboard
  src/components/          ribbon, chart, history table, scrape log, search
docs/
  DEPLOY.md                step-by-step deployment
  DESIGN_NOTE.md           reliability decisions, trade-offs, AI corrections
```

---

## Scope and honesty

Written as a first-round assignment for INE. Only the provided mock store is
scraped. Things deliberately left out: user accounts, email alerts (alerts are
in-app), and any caching layer beyond the learned endpoint.
