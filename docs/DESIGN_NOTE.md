# Design note

## The constraint that shaped everything

I started by fetching the store's HTML directly. It comes back with a `<title>`
and essentially nothing else — the entire catalogue is rendered client-side.

That put me in front of an apparent contradiction in the brief. It asks me to
*prefer lightweight HTTP fetching* and *reach for a headless browser only where
the page genuinely requires it*. Taken at face value, this page genuinely
requires a browser. But running Chromium every two hours for every tracked
product on a 512 MB free-tier instance is slow, memory-hungry, and the most
fragile option available.

The way out: a JavaScript store still has to get its data from somewhere. Its
own frontend makes network calls. So the browser is used **once, as a teacher**.

## Architecture: a ladder that learns

Three strategies, tried in order, with the one that worked last time promoted to
first place for the next run:

1. `json_api` — call the JSON endpoint, one plain HTTP request
2. `http_html` — fetch and parse the HTML, including state embedded in `<script>` tags
3. `browser` — render in headless Chromium

The browser strategy attaches a listener to every response the page receives. Any
JSON response whose URL looks like product data and whose body contains a list of
records is saved to a `SiteProfile` row. The next scheduled run finds that
endpoint waiting for it and never starts a browser.

So the steady state is a single HTTP request per product. The browser is the
fallback, and also the recovery path: if the learned endpoint starts returning
404 because the store changed, the ladder falls through to the browser, which
re-learns the new endpoint and saves it. The system repairs itself without a
deploy.

This is the part of the design I would defend hardest. It is not a clever trick
— it is the only way I found to honour both halves of the brief at once.

## Reliability decisions

**Retries are classified, not blanket.** Timeouts, connection errors and 5xx
responses are retried with backoff at 2s, 5s and 11s, each with ±40% jitter. A
404, or a page that renders fine but contains no price, is permanent — retrying
it four times wastes 18 seconds to arrive at the same answer. Permanent errors
fall through to the next strategy immediately.

The jitter matters more than it looks. Without it, every product that fails at
the same moment retries at the same moment, which turns a brief wobble in the
store into a synchronised hammer.

**Waiting for the right thing.** The obvious way to handle late-loading content
is `wait_for_load_state("networkidle")`. That is a proxy for what I actually
care about, and a bad one — the network can go quiet before the price element is
populated, and on a slow response it never goes quiet at all. So the browser
strategy polls for a *parseable price*, and requires it to be stable across two
reads about 600 ms apart before accepting it. `networkidle` is still used, but
with a short timeout and a shrug if it expires.

**The validator is the whole point.** The brief says the scraper must never
store wrong or empty data. I put a single gate in front of the database rather
than trusting each strategy:

- The price must parse to a positive number in a plausible range.
- Ambiguous text returns `None` instead of a guess. If an element contains both
  an MRP and a live price, the parser refuses it rather than picking one. An
  honest failure row is better than a plausible wrong number, because a wrong
  number is invisible forever afterwards.
- If a new reading is more than 40% from the rolling median of the last nine, it
  is not trusted on its own. The scraper re-fetches using a *different*
  strategy. Agreement means the outlier is a real price change and it is stored.
  Disagreement means something is broken, and the attempt is logged as
  `rejected` with nothing written to history.

That last rule is the one I am least sure about, and it is a genuine trade-off —
see below.

**Failures are first-class rows.** `ScrapeAttempt` records the outcome, the
number of tries, the duration, the error, and a `trace` array with a line per
retry and per backoff. The UI shows the trace expanded on demand. Failures are
not hidden, not silently skipped, and not padded with the previous price.

**Structure drift.** After each success, a hash of the page's shape — which
selectors matched and their ancestor tag/class path — is compared to the last
one. A price change does not move it; a redesign does. When it moves, an alert
is raised. The scraper does not stop; it flags.

## Trade-offs I made

**Anomaly confirmation can suppress a real crash in price.** If the store
genuinely halves a price, the first reading is rejected, and the confirmation
fetch has to agree before it is stored. If both reads succeed this costs one
extra request and nothing else. But if the confirming strategy happens to fail,
a real price change is recorded as `rejected` and lost until the next run two
hours later. I decided a two-hour delay on a real change is cheaper than a
permanent wrong number in the history, given the brief weights correctness over
completeness. A production system would store it as `unconfirmed` rather than
discarding it.

**Chromium on a 512 MB instance.** If the learned endpoint holds, the browser
never runs in production and this is free. If it does not, a browser launch on
Render's free tier can be killed by the OOM reaper. The ladder degrades to
"failed, logged honestly" rather than crashing the run, and `PLAYWRIGHT_ENABLED`
can turn the fallback off entirely. Accepted rather than solved.

**Selector lists instead of one selector.** Every element is looked up through a
candidate list plus a regex text-scan as a last resort. This is more robust and
less precise — a wide net can catch the wrong fish. The validator is what makes
this safe: a wrong catch usually produces an implausible number, and implausible
numbers do not get stored.

**No email alerts.** Alerts are in-app. SendGrid was a bonus item and I would
rather submit a scraper I trust than a notification feature I rushed.

**A synchronous cron endpoint.** `/api/cron/scrape/` does the work in the
request rather than queueing it. With a handful of products and ~1s per
lightweight scrape this is fine, and it means one moving part instead of three.
It would not survive fifty products, at which point the right answer is a task
queue, not a longer timeout.

## What the AI tools got wrong first time, and how I corrected it

I used Claude heavily while building this. The useful failures:

**It reached straight for Playwright on everything.** The first design it
produced launched a browser for every product on every run, because the served
HTML is empty and that is the obvious conclusion. It was a working scraper and a
bad one — slow, memory-hungry, and guaranteed to die on a free tier. The fix was
mine: use the browser once to discover the store's own JSON endpoint, cache it,
and serve every subsequent run over plain HTTP. That reframing is what turned a
naive scraper into one that fits the brief's constraints.

**It treated `networkidle` as "the content has loaded".** The generated waiting
logic was `page.goto(...); page.wait_for_load_state("networkidle"); read price`.
Against a store that loads content asynchronously after a delay, this reads
whatever is in the DOM at the moment the network happens to go quiet — which on
a slow response is an empty element or a placeholder. I replaced it with polling
for a parseable price and requiring it to be stable across two reads.

**Its parser guessed.** The first `parse_price` returned `0` when it could not
find a number, and grabbed the first number it saw when there were several.
Both are exactly the bug the brief warns about: `0` would have gone into the
history as a real price, and the first number in `₹2,499 ₹3,999` is not always
the one being charged. I rewrote it to return `None` on ambiguity, take the
lowest plausible candidate when several are present, and refuse text blobs that
mix "MRP" with a live price. This is now the most heavily tested file in the
project.

**It wrote `except Exception: pass` around the scrape loop.** The stated intent
was "so one product doesn't break the run", which is correct — but swallowing
the exception silently is precisely the "never silently stop" failure mode. I
kept the catch and made every branch write a trace row, so the run continues
*and* the log says why.

**It offered to hard-code selectors I had not verified.** Since I could not see
the rendered DOM from outside a browser, it happily proposed `.product-price`
and friends as though they were known. I turned those into candidate lists and
wrote `manage.py probe_store` to check them against the real rendered page and
report which ones actually match, so the guesses are verified rather than
assumed.

The pattern across all five: the model produces code that runs, and is
optimistic about the world. Every correction I made was in the same direction —
making the scraper assume less and admit more.
