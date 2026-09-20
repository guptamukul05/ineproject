# Product Price Tracker

A full-stack web app that lets you pick a product from a store, track it, and
watch its price and stock change over time. The scraper is built to run
unattended on a schedule and to be honest about it when it fails — a bad
scrape shows up in the log as a failure, it never quietly writes a wrong
number into the history.



---

## What it does

- **Search and track** — search the store by partial or full product name and
  add a product to your watchlist.
- **Scheduled scraping** — each tracked product is scraped on a fixed
  interval (default every 2 hours, configurable per product from 30 minutes
  up to a day).
- **Price and stock history** — shown as a line chart or a plain table.
- **A scrape log you can actually trust** — every attempt is recorded with a
  timestamp, an outcome (success / retried / rejected / failed), how many
  tries it took, and a step-by-step trace of what happened on each try. No
  data is stored on a failed attempt.
- **A headed, observable run** — the scraper can be run in a visible browser
  window with an on-page overlay narrating what it's doing, for demonstrating
  how it behaves against a slow or failing response.

### Bonus features implemented

- In-app alerts for price drops/rises, back-in-stock, out-of-stock, and page
  structure changes.
- A dashboard across all tracked products, not just one.
- Change detection that flags when the store's page structure shifts under
  the scraper.
- Configurable scrape frequency per product.
- CI via GitHub Actions — every push runs the Django checks, the migration
  check, the parser/validator tests, and a frontend production build.

---

## Why the scraping is built the way it is

The store's HTML, fetched directly, comes back with almost nothing in it —
the whole catalogue is rendered client-side by JavaScript. On top of that,
the individual product pages don't show the price immediately: they hide it
behind a "reveal" interaction and only populate it a few seconds later,
sometimes needing a genuine mouse hover first, sometimes just needing more
time. So a scraper that works here has to deal with two separate problems:
content that isn't in the served HTML at all, and content that loads late
even once you're in a real browser.

### A strategy ladder that learns

Each product is scraped by trying up to three approaches, in order, and
whichever one worked last time is tried first on the next run:

1. **`json_api`** — call whatever JSON endpoint the store's own frontend
   uses, one plain HTTP request.
2. **`http_html`** — fetch the page and parse the HTML, including any state
   embedded directly in `<script>` tags.
3. **`browser`** — render the page in headless Chromium, wait out the reveal
   interaction, and read the price once it actually appears.

The browser strategy also listens to the page's own network traffic while it
runs. Any JSON response that looks like product data gets cached, so a
future run can try the lightweight HTTP path first instead of paying for a
browser every time. The browser is the fallback and the teacher, not the
default.

### Reading a revealed price without guessing at selectors

Rather than hard-coding where the price element lives (which is exactly the
kind of assumption a page redesign breaks), the browser strategy snapshots
every currency-shaped piece of text visible on the page before doing
anything, then watches for new currency-shaped text to appear afterward and
holds steady for half a second before trusting it. This works whether the
price shows up because of a click, because of a delay, or because of
something else entirely — the scraper doesn't need to know which in advance.

### Retries that know the difference between "try again" and "give up"

Timeouts, connection errors, and 5xx responses are retried with exponential
backoff (2s / 5s / 11s) plus jitter, so a cluster of products failing at the
same moment doesn't all retry in lockstep. A 404, or a page that loads fine
but genuinely has no price on it, is treated as permanent and the scraper
moves straight to the next strategy instead of burning the retry budget on
something that won't change.

### Nothing untrustworthy reaches the price history

Every reading has to pass validation before it's written to the database:

- The price has to parse to a positive number in a plausible range —
  ambiguous text (an MRP mixed in with a live price, for instance) returns
  "couldn't read this" rather than a guess.
- If a new price is far off the recent rolling median, one reading isn't
  enough on its own. The scraper re-fetches using a *different* strategy; if
  the second read agrees, the price change is real and gets stored, and if
  it disagrees, the attempt is logged as rejected and nothing is written.

A failed or rejected scrape leaves a gap in the chart. It never leaves a
wrong number in it.

### Catching a redesign, not just a bad response

After each successful scrape, the scraper hashes a coarse description of the
page's shape — which selectors matched and the tag/class path leading to
them. An ordinary price change doesn't move this hash; a redesign does. When
it moves, an alert is raised instead of the scraper quietly reading the
wrong element going forward.

---

## Project structure

```
backend/
  config/                   Django settings, URLs, WSGI
  tracker/
    models.py               Product, PricePoint, ScrapeAttempt, ScrapeRun, Alert, SiteProfile
    views.py                REST API
    tests.py                parser and validator tests
    scraper/
      engine.py              the strategy ladder, retries, validation, persistence
      http_strategy.py       json_api and http_html strategies
      browser_strategy.py    Playwright strategy, network learning, headed overlay
      parsing.py             turning price/stock text into values
      validators.py          the gate before anything is written to history
      fingerprint.py         structure-change detection
      chaos.py               fault injection, used only for the demo run
      config.py               selectors, retry policy, thresholds
    management/commands/
      probe_store.py          inspect what the scraper can see on the store
      inspect_reveal.py       dump the DOM before/after the reveal interaction
      seed_demo.py             track a handful of products to get started
      scrape_now.py            run a scheduled scrape headlessly
      scrape_headed.py         run a scrape in a visible browser with an overlay
frontend/
  src/App.jsx                 dashboard
  src/components/             reliability ribbon, chart, history table, scrape log, search
docs/
```

---

## Local setup

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env             # fill in DJANGO_SECRET_KEY and CRON_SECRET
python manage.py migrate
python manage.py test tracker    # parser + validator tests
python manage.py probe_store     # see what the scraper can find on the store
python manage.py seed_demo       # track a few products and scrape once
python manage.py runserver
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env             # set VITE_API_BASE=http://127.0.0.1:8000
npm run dev
```

Open `http://localhost:5173`.

### Useful commands

```bash
python manage.py probe_store             # inspect the store, save debug artefacts
python manage.py inspect_reveal --product 1   # ground-truth dump of the reveal interaction
python manage.py seed_demo               # track the first few products and scrape once
python manage.py scrape_now --force      # scrape everything, headless
python manage.py scrape_headed           # visible browser, with an on-page overlay
python manage.py scrape_headed --chaos   # ...plus injected slow/failing responses, for demoing retries
python manage.py test tracker            # run the test suite
```

---

## Deployment

The backend is a standard Django app, the frontend is a static Vite build,
and the database is Postgres — any host that runs those works. This is how
it's set up:

### Database

A managed Postgres instance (Supabase works well on a free tier). Use the
**session pooler** connection string rather than the direct one — the direct
connection is IPv6-only and many free-tier app hosts can't reach it. Put the
URI in `DATABASE_URL`.

### Backend

Any Python host that supports a custom build/start command works. The build
step needs to:

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
python manage.py collectstatic --no-input
python manage.py migrate
```

and the start command:

```bash
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120
```

Set the environment variables listed below on the host. `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` in `config/settings.py` already allow `.onrender.com`
and `.vercel.app` — add your own domain via `EXTRA_ALLOWED_HOST` if you're
hosting elsewhere.

If the host's free tier can't install Chromium during the build, set
`PLAYWRIGHT_ENABLED=false`. The two HTTP-based strategies keep working; only
the browser fallback is disabled.

### Frontend

Any static host that runs a Vite build works — point the build command at
`npm run build`, the output directory at `dist`, and set `VITE_API_BASE` to
the deployed backend's URL (no trailing slash). `frontend/vercel.json`
rewrites all routes to `index.html`, which a single-page app needs on most
static hosts.

### Health check

`GET /api/health/` returns the current status, the configured scrape
interval, how many products are tracked, and the last completed run — useful
both as a host's health-check endpoint and as a quick way to confirm the
deployment is wired up correctly.

---

## Scraping schedule

Scraping does not run as an always-on background loop — free-tier app hosts
sleep after a period of inactivity, so an always-on loop would just stop.
Instead, the backend exposes:

```
GET /api/cron/scrape/?key=YOUR_CRON_SECRET
```

Calling this endpoint scrapes every tracked product that is currently due.
"Due" is per-product: each product has its own interval (default 2 hours,
adjustable from the UI between 30 minutes and 1 day), and a product is only
scraped once its interval has actually elapsed since its last successful
read — calling the endpoint more often than that just checks and finds
nothing due yet, it doesn't force extra scrapes.

An external scheduler is what actually calls this on a timer — a free
service like cron-job.org, set to hit the URL above every 2 hours, works
well and also has the side effect of waking a sleeping host. The key must be
sent either as `?key=`, as an `X-Cron-Key` header, or in a JSON body, and has
to match `CRON_SECRET` — a request with the wrong key gets a 401 and nothing
is scraped.

Any individual product can also be scraped immediately, ignoring its
schedule, from the dashboard's "Scrape now" button, or directly via:

```
POST /api/products/<id>/scrape/
```

---

## Environment variables

### Backend

| Variable | Required | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | yes | any long random string |
| `DJANGO_DEBUG` | no | `true` for local dev, `false` in production |
| `DATABASE_URL` | no | leave blank for local SQLite; set to a Postgres URI (session pooler) in production |
| `CRON_SECRET` | yes | shared secret the scheduler must send to `/api/cron/scrape/` |
| `STORE_BASE_URL` | no | defaults to the demo store above |
| `SCRAPE_INTERVAL_MINUTES` | no | default interval for newly tracked products, defaults to `120` |
| `PLAYWRIGHT_ENABLED` | no | set `false` to disable the browser fallback |
| `CORS_ALLOWED_ORIGINS` | no | comma-separated list of frontend origins allowed to call the API |
| `CORS_ALLOW_ALL` | no | `true` for local dev convenience |
| `EXTRA_ALLOWED_HOST` | no | add a custom backend domain to `ALLOWED_HOSTS` |

### Frontend

| Variable | Required | Notes |
|---|---|---|
| `VITE_API_BASE` | yes | backend URL, no trailing slash |

Copy `.env.example` to `.env` in each of `backend/` and `frontend/` and fill
these in — never commit the real `.env` files.

---

