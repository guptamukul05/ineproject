const LABELS = {
  success: "read cleanly",
  retried: "succeeded after a retry",
  rejected: "read, but refused by validation",
  failed: "no reading taken",
};

export default function ReliabilityRibbon({ attempts }) {
  // oldest on the left, so the ribbon reads like a timeline
  const ticks = [...attempts].reverse().slice(-60);

  if (!ticks.length) {
    return <p className="empty">No scrape attempts recorded yet.</p>;
  }

  return (
    <>
      <div className="ribbon" role="img" aria-label={`Last ${ticks.length} scrape outcomes`}>
        {ticks.map((a, i) => (
          <span
            key={a.id}
            className={`tick ${a.outcome}`}
            style={{ animationDelay: `${Math.min(i * 9, 500)}ms` }}
            title={`${new Date(a.created_at).toLocaleString()} — ${LABELS[a.outcome]}${
              a.attempts > 1 ? ` (${a.attempts} tries)` : ""
            }${a.error ? `\n${a.error}` : ""}`}
          />
        ))}
      </div>
      <div className="ribbon-key">
        {Object.entries(LABELS).map(([key, label]) => (
          <span key={key}>
            <i className={`tick ${key}`} style={{ height: 9 }} />
            {label}
          </span>
        ))}
      </div>
    </>
  );
}
