const WORDING = {
  success: "success",
  retried: "retried",
  failed: "failed",
  rejected: "rejected",
};

export default function ScrapeLog({ attempts, currency }) {
  if (!attempts.length) {
    return <p className="empty">No scrape attempts logged for this product yet.</p>;
  }

  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Outcome</th>
            <th>Tries</th>
            <th>Took</th>
            <th>Read</th>
            <th>What happened</th>
          </tr>
        </thead>
        <tbody>
          {attempts.map((a) => (
            <tr key={a.id}>
              <td>{new Date(a.created_at).toLocaleString()}</td>
              <td>
                <span className={`badge ${a.outcome}`}>{WORDING[a.outcome]}</span>
              </td>
              <td>{a.attempts}</td>
              <td>{(a.duration_ms / 1000).toFixed(1)}s</td>
              <td>{a.price_found ? `${currency}${a.price_found}` : "—"}</td>
              <td className="reason">
                {a.error || `Read by the ${a.strategy || "?"} strategy.`}
                {a.trace?.length > 0 && (
                  <details className="trace">
                    <summary>attempt trace ({a.trace.length} steps)</summary>
                    <pre>{JSON.stringify(a.trace, null, 1)}</pre>
                  </details>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
