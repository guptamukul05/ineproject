import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const fmtTime = (iso) =>
  new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

export default function PriceChart({ points, currency }) {
  if (!points.length) {
    return (
      <p className="empty">
        Nothing charted yet. A point appears here only after a scrape passes
        validation, so an empty chart means no reading has been trusted.
      </p>
    );
  }

  const data = points.map((p) => ({
    t: fmtTime(p.scraped_at),
    price: Number(p.price),
    stock: p.in_stock,
  }));

  return (
    <div className="chart-holder">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 14, bottom: 4, left: 4 }}>
          <CartesianGrid stroke="var(--hairline)" vertical={false} />
          <XAxis
            dataKey="t"
            tick={{ fontSize: 11, fill: "var(--ink-soft)", fontFamily: "var(--mono)" }}
            minTickGap={28}
            stroke="var(--hairline-strong)"
          />
          <YAxis
            width={64}
            domain={["auto", "auto"]}
            tick={{ fontSize: 11, fill: "var(--ink-soft)", fontFamily: "var(--mono)" }}
            stroke="var(--hairline-strong)"
          />
          <Tooltip
            contentStyle={{
              background: "var(--panel)",
              border: "1px solid var(--hairline-strong)",
              borderRadius: 4,
              fontFamily: "var(--mono)",
              fontSize: 12,
              color: "var(--ink)",
            }}
            formatter={(v, _n, entry) => [
              `${currency || ""}${v}${
                entry.payload.stock === false ? "  (out of stock)" : ""
              }`,
              "price",
            ]}
          />
          <Line
            type="stepAfter"
            dataKey="price"
            stroke="var(--ink)"
            strokeWidth={1.8}
            dot={{ r: 2.2, fill: "var(--ink)" }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
