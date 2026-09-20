import { useState } from "react";
import { api } from "../api";

export default function SearchPanel({ onTracked, trackedIds }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function search(e) {
    e?.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await api.search(query);
      setResults(data.results);
    } catch (err) {
      setError(err.message);
      setResults(null);
    } finally {
      setBusy(false);
    }
  }

  async function track(item) {
    setError("");
    try {
      await api.track({
        store_product_id: item.store_product_id,
        name: item.name,
        url: item.url,
        image_url: item.image_url,
        category: item.category,
      });
      onTracked();
      setResults((rs) =>
        rs.map((r) =>
          r.store_product_id === item.store_product_id
            ? { ...r, already_tracked: true }
            : r
        )
      );
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Find a product</h2>
      </div>
      <div className="panel-body">
        <form className="search-row" onSubmit={search}>
          <input
            type="text"
            value={query}
            placeholder="Part of a product name"
            aria-label="Search the store by product name"
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Searching" : "Search"}
          </button>
        </form>

        {error && (
          <p className="notice bad" style={{ marginTop: 12 }}>
            {error}
          </p>
        )}

        {results && results.length === 0 && (
          <p className="empty" style={{ marginTop: 12 }}>
            Nothing in the store matches that name. Try a shorter fragment, or
            search with an empty box to list everything.
          </p>
        )}

        {results?.length > 0 && (
          <div style={{ marginTop: 12 }}>
            {results.map((r) => {
              const tracked =
                r.already_tracked || trackedIds.includes(r.store_product_id);
              return (
                <div className="result" key={r.store_product_id}>
                  <div className="who">
                    <strong>{r.name}</strong>
                    <span>
                      {r.price ? `listed at ${r.price}` : "price hidden on listing"}
                      {r.category ? ` · ${r.category}` : ""}
                    </span>
                  </div>
                  <button
                    className="btn small quiet"
                    disabled={tracked}
                    onClick={() => track(r)}
                  >
                    {tracked ? "Tracking" : "Track"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
