const BASE = (import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000").replace(/\/$/, "");

async function call(path, options = {}) {
  const res = await fetch(`${BASE}/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = `Request failed with ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  base: BASE,
  health: () => call("/health/"),
  dashboard: () => call("/dashboard/"),
  search: (q) => call(`/store/search/?q=${encodeURIComponent(q)}`),
  track: (product) =>
    call("/products/", { method: "POST", body: JSON.stringify(product) }),
  untrack: (id) => call(`/products/${id}/`, { method: "DELETE" }),
  update: (id, patch) =>
    call(`/products/${id}/`, { method: "PATCH", body: JSON.stringify(patch) }),
  history: (id) => call(`/products/${id}/history/`),
  logs: (id) => call(`/products/${id}/logs/`),
  scrapeNow: (id) => call(`/products/${id}/scrape/`, { method: "POST" }),
  alerts: () => call("/alerts/"),
  ackAlert: (id) => call(`/alerts/${id}/ack/`, { method: "POST" }),
};
