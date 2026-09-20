# Design note

## The constraint that shaped everything

The first thing I did was fetch the store's HTML directly, without a
browser. It came back with basically nothing in it — a page title and not
much else. The whole catalogue is rendered client-side. That one fact
decided the shape of the rest of the project.

On top of that, once I actually got a browser rendering the page, individual
product pages still didn't show a price right away. They sit behind a
"reveal" interaction, and even after that the price doesn't always show up
immediately — sometimes it needs a real mouse hover before it does anything,
sometimes it just takes a few seconds regardless. I only found this out by
running the scraper in headed mode and watching it happen live, which is
part of why the headed-mode command exists as more than just a nice-to-have
for the recording — it's genuinely how I debugged the hardest part of this.

## Architecture: a ladder that learns

Three strategies are tried in order for each product, and whichever one
worked last time gets promoted to first place on the next run:

1. `json_api` — call the JSON endpoint the store's own frontend uses, one
   plain HTTP request.
2. `http_html` — fetch and parse the HTML, including any state sitting
   directly in `<script>` tags.
3. `browser` — render the page in headless Chromium.

While the browser strategy runs, it listens to every response the page
receives. Any JSON response that looks like a list of product records gets
cached. From then on, the lightweight `json_api` strategy tries that
endpoint first and the browser doesn't have to start at all. In practice the
catalogue-level data was available this way, but the actual price per
product turned out to be deliberately withheld from any JSON response until
the reveal interaction happens — so for price specifically, the browser
strategy ends up doing the real work most of the time, and the ladder exists
mainly so a cheaper path is tried first when it can succeed.

## Reliability decisions

**Detecting the price by what changed, not by where it lives.** My first
version of the browser strategy looked for the price inside a fixed list of
CSS selectors. It worked inconsistently — sometimes the click on "reveal
price" would time out entirely and the price would still show up a few
seconds later somewhere else on the page; sometimes the selectors just
didn't match whatever element the store actually used. I replaced that
approach with something selector-independent: snapshot every currency-shaped
bit of text visible before doing anything, then watch for *new*
currency-shaped text to appear afterward, and only trust it once it's held
steady for half a second. This turned out to be more robust than trying to
predict the exact DOM shape, because it doesn't actually matter whether the
price arrives via a click, a delay, or something else — the detection
doesn't care how it got there.

**Retries that know the difference between "try again" and "this won't
change."** Timeouts, connection errors, and 5xx responses are retried with
backoff at 2s, 5s, and 11s, with jitter added so that several products
failing at the same moment don't all retry in lockstep. A 404, or a page
that renders fine but genuinely has no price on it, is treated as permanent
and the scraper moves on to the next strategy immediately rather than
spending four attempts to arrive at the same answer.

**Waiting for the right thing, not a proxy for it.** The easy way to handle
late-loading content is to wait for the network to go idle and then read
whatever's on the page. That's a weak signal here — the network can go quiet
before the price is actually populated, and on a slow response it might
never go quiet within a reasonable window at all. So the scraper polls for
an actual parseable price instead, and gives that up to 25 seconds, since
the assignment description explicitly says responses can be slow.

**A single gate in front of the database.** Every reading has to pass
through validation before it's written anywhere:

- The price has to parse to a positive number in a sane range. Ambiguous
  text — a listing that mixes a struck-through original price with the
  current one, say — comes back as "couldn't read this" rather than a guess,
  because a wrong number stored silently is worse than an honest gap.
- If a new price is far off the recent rolling median, one reading isn't
  trusted by itself. The scraper re-fetches using a different strategy;
  agreement means the price genuinely changed and it gets stored, and
  disagreement means the attempt is logged as rejected with nothing written.

**Failures are rows, not silence.** Every attempt — successful or not — gets
logged with a timestamp, an outcome, how many tries it took, and a
step-by-step trace. The scrape log is meant to be read honestly: a run that
failed shows up as failed, not as a stale price sitting there looking fine.

**Catching a redesign.** After a successful scrape, the scraper hashes a
coarse description of the page's shape — which selectors matched and the
tag/class path down to them. A price changing doesn't move this hash; the
page being restructured does. When it moves, an alert gets raised instead of
the scraper quietly reading whatever happens to be in that spot from then on.

## Trade-offs I made

**Confirming an outlier can delay a real price crash.** If the store
genuinely drops a price sharply, the first reading gets rejected pending
confirmation, and if the confirming fetch happens to fail for an unrelated
reason, a real change ends up logged as rejected and isn't picked up again
until the next scheduled run. I decided that was an acceptable trade against
the alternative of a single bad reading permanently poisoning the history,
given how much the brief weights correctness over completeness.

**The browser is the expensive path, and it's the one that matters most
here.** Because the price genuinely isn't obtainable any other way on this
particular store, the browser strategy isn't really an occasional fallback
in practice — it's doing most of the real work. That's more resource-heavy
than I'd have liked, but it was a decision forced by how the store is built,
not a choice I'd have made if the price had been reachable more cheaply.

**Wide selector lists instead of one precise selector.** Every DOM lookup
that isn't the price itself (name, stock, catalogue cards) goes through a
list of plausible candidates plus a text-pattern fallback, rather than one
hard-coded selector. That's less precise on its own, which is exactly why
the validation layer exists — an implausible catch is far more likely to get
rejected before it ever reaches the price history.

## What went wrong on the first attempt, and how I fixed it

**The first version launched a browser for every scrape, every time.**
Given that the raw HTML is empty, that was the obvious starting point, and
it worked — but it would have meant spinning up Chromium for every tracked
product on every scheduled run, which is slow and heavy for what's supposed
to be a small, unattended job. The fix was to have the browser teach the
lightweight path instead of being the path itself: watch what JSON the page
fetches for itself, cache the useful endpoint, and only fall back to the
browser when the cheap path doesn't have what it needs.

**The waiting logic trusted "network idle" as a stand-in for "the price is
there."** Against a store that deliberately reveals content late, that
assumption reads whatever happens to be on the page at the moment the
network quiets down — which can easily be nothing. I rewrote it to poll for
an actual parseable price and wait it out properly rather than trusting a
timing heuristic.

**The price parser used to guess when it wasn't sure.** An early version
returned 0 when it couldn't find a number, and grabbed the first number it
saw when a listing showed more than one price on the page (an original price
next to a discounted one, for example). Both are exactly the failure mode
the brief warns about — a `0` would have gone straight into the price
history looking like real data, and grabbing the first number isn't always
grabbing the right one. I rewrote it to return nothing rather than a guess
when the text is ambiguous, and to prefer the lowest plausible number when
several appear together, since that's the one actually being charged.

**The reveal-price detection was hard-coded to a specific click flow, and it
broke as soon as the store didn't need a click.** This was the trickiest bug
to catch, because it only showed up as every single scrape failing with "no
price found," with no obvious cause from the logs alone. Watching it run in
headed mode made it clear: sometimes the click itself timed out, and the
price still appeared a few seconds later regardless. The fix was to stop
assuming *how* the price would appear and just watch *whether* it did —
comparing the page's text before and after, rather than depending on a
specific interaction succeeding.

The pattern across all of these is the same: the early versions were
optimistic about what the page would do, and every fix in the same
direction — making the scraper assume less about the page's behavior and
verify more of what actually happened before trusting it.