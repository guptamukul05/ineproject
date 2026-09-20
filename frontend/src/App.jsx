import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import HistoryTable from "./components/HistoryTable";
import PriceChart from "./components/PriceChart";
import ReliabilityRibbon from "./components/ReliabilityRibbon";
import ScrapeLog from "./components/ScrapeLog";
import SearchPanel from "./components/SearchPanel";

function sinceText(iso) {
  if (!iso) return "never scraped";
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 48 ? `${hrs} h ago` : `${Math.round(hrs / 24)} d ago`;
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [dash, setDash] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [view, setView] = useState("chart");
  const [error, setError] = useState("");
  const [scraping, setScraping] = useState(false);

  const loadDash = useCallback(async () => {
    try {
      const data = await api.dashboard();
      setDash(data);
      setSelectedId((cur) => cur ?? data.products[0]?.id ?? null);
      setError("");
    } catch (err) {
      setError(
        `The backend at ${api.base} did not respond (${err.message}). On Render's ` +
          `free tier the first request after a sleep can take up to a minute.`
      );
    }
  }, []);

  const loadDetail = useCallback(async (id) => {
    if (!id) return setDetail(null);
    const [history, logs] = await Promise.all([api.history(id), api.logs(id)]);
    setDetail({ ...history, ...logs });
  }, []);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    loadDash();
    const t = setInterval(loadDash, 60000);
    return () => clearInterval(t);
  }, [loadDash]);

  useEffect(() => {
    loadDetail(selectedId).catch((e) => setError(e.message));
  }, [selectedId, loadDetail]);

  async function scrapeNow() {
    setScraping(true);
    try {
      await api.scrapeNow(selectedId);
      await Promise.all([loadDash(), loadDetail(selectedId)]);
    } catch (err) {
      setError(err.message);
    } finally {
      setScraping(false);
    }
  }

  async function untrack(id) {
    await api.untrack(id);
    setSelectedId(null);
    setDetail(null);
    loadDash();
  }

  async function setInterval_(id, minutes) {
    await api.update(id, { interval_minutes: minutes });
    loadDash();
    loadDetail(id);
  }

  const products = dash?.products ?? [];
  const product = detail?.product;
  const totals = dash?.totals ?? {};
  const trackedIds = products.map((p) => p.store_product_id);

  return (
    <div className="shell">
      <header className="masthead">
        <div>
          <h1>Price watch</h1>
          <p>
            Every two hours this app reads the INE mock store and writes down what
            it found. When a read cannot be trusted, it records the failure
            instead of guessing a number.
          </p>
        </div>
        <div className="pulse">
          <span className={`dot ${health ? "" : "down"}`} />
          {health
            ? `backend up · every ${health.interval_minutes} min · ${health.tracked_products} tracked`
            : "backend unreachable"}
        </div>
      </header>

      {error && <p className="notice bad" style={{ marginTop: 18 }}>{error}</p>}

      <div className="columns">
        <div>
          <SearchPanel onTracked={loadDash} trackedIds={trackedIds} />

          <section className="panel">
            <div className="panel-head">
              <h2>Watchlist</h2>
              <span className="pulse">{products.length}</span>
            </div>
            {products.length === 0 ? (
              <div className="panel-body">
                <p className="empty">
                  Search above and pick a product to start a price history.
                </p>
              </div>
            ) : (
              <ul className="watch">
                {products.map((p) => (
                  <li key={p.id}>
                    <button
                      aria-current={p.id === selectedId}
                      onClick={() => setSelectedId(p.id)}
                    >
                      <span className="line">
                        <span className="name">{p.name}</span>
                        <span className="price">
                          {p.last_price ? `${p.currency}${p.last_price}` : "—"}
                        </span>
                      </span>
                      <span className="meta">
                        {sinceText(p.last_success_at)}
                        {p.reliability !== null ? ` · ${p.reliability}% clean` : ""}
                        {p.consecutive_failures > 0
                          ? ` · ${p.consecutive_failures} failing`
                          : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {dash?.alerts?.length > 0 && (
            <section className="panel">
              <div className="panel-head">
                <h2>Needs a look</h2>
              </div>
              <div className="panel-body">
                <ul className="alerts">
                  {dash.alerts.map((a) => (
                    <li key={a.id}>
                      <span className={`badge ${
                        a.kind === "price_drop" || a.kind === "back_in_stock"
                          ? "success"
                          : a.kind === "structure_change"
                          ? "rejected"
                          : "retried"
                      }`}>
                        {a.kind.replace(/_/g, " ")}
                      </span>
                      <span>{a.message}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </section>
          )}
        </div>

        <div>
          {!product ? (
            <div className="panel">
              <div className="panel-body">
                <p className="empty">
                  Pick a product from the watchlist to see its price history and
                  scrape log.
                </p>
              </div>
            </div>
          ) : (
            <>
              <section className="panel">
                <div className="panel-head">
                  <h2>{product.name}</h2>
                  <div className="tabs">
                    <button className="btn small quiet" onClick={scrapeNow} disabled={scraping}>
                      {scraping ? "Scraping…" : "Scrape now"}
                    </button>
                    <button className="btn small quiet" onClick={() => untrack(product.id)}>
                      Stop tracking
                    </button>
                  </div>
                </div>
                <div className="panel-body">
                  <ReliabilityRibbon attempts={detail.attempts ?? []} />
                </div>
                <dl className="figures">
                  <div>
                    <dt>Latest price</dt>
                    <dd>
                      {product.last_price
                        ? `${product.currency}${product.last_price}`
                        : "—"}
                    </dd>
                  </div>
                  <div>
                    <dt>Stock</dt>
                    <dd>
                      {product.last_in_stock === null
                        ? "unknown"
                        : product.last_in_stock
                        ? "in"
                        : "out"}
                    </dd>
                  </div>
                  <div>
                    <dt>Readings kept</dt>
                    <dd>{product.points_recorded}</dd>
                  </div>
                  <div>
                    <dt>Clean reads</dt>
                    <dd>
                      {product.reliability === null ? "—" : `${product.reliability}%`}
                    </dd>
                  </div>
                  <div>
                    <dt>Read by</dt>
                    <dd style={{ fontSize: 14 }}>{product.preferred_strategy}</dd>
                  </div>
                </dl>
              </section>

              <section className="panel">
                <div className="panel-head">
                  <h2>Price and stock over time</h2>
                  <div className="tabs">
                    <button
                      aria-pressed={view === "chart"}
                      onClick={() => setView("chart")}
                    >
                      Chart
                    </button>
                    <button
                      aria-pressed={view === "table"}
                      onClick={() => setView("table")}
                    >
                      Table
                    </button>
                  </div>
                </div>
                <div className="panel-body">
                  {view === "chart" ? (
                    <PriceChart points={detail.points} currency={product.currency} />
                  ) : (
                    <HistoryTable points={detail.points} currency={product.currency} />
                  )}
                </div>
              </section>

              <section className="panel">
                <div className="panel-head">
                  <h2>Scrape log</h2>
                  <span className="pulse">
                    {totals.success ?? 0} clean · {totals.retried ?? 0} retried ·{" "}
                    {totals.rejected ?? 0} rejected · {totals.failed ?? 0} failed
                  </span>
                </div>
                <div className="panel-body">
                  <div className="spread" style={{ marginBottom: 14 }}>
                    <label
                      style={{ fontSize: 13, color: "var(--ink-soft)" }}
                      htmlFor="interval"
                    >
                      Check this product every
                    </label>
                    <select
                      id="interval"
                      style={{ width: 160 }}
                      value={product.interval_minutes}
                      onChange={(e) =>
                        setInterval_(product.id, Number(e.target.value))
                      }
                    >
                      <option value={30}>30 minutes</option>
                      <option value={60}>1 hour</option>
                      <option value={120}>2 hours (default)</option>
                      <option value={360}>6 hours</option>
                      <option value={1440}>1 day</option>
                    </select>
                  </div>
                  <ScrapeLog
                    attempts={detail.attempts ?? []}
                    currency={product.currency}
                  />
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
