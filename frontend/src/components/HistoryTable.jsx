export default function HistoryTable({ points, currency }) {
  if (!points.length) return <p className="empty">No stored readings yet.</p>;
  const rows = [...points].reverse();

  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Recorded</th>
            <th>Price</th>
            <th>Stock</th>
            <th>Read by</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id}>
              <td>{new Date(p.scraped_at).toLocaleString()}</td>
              <td>
                {currency}
                {p.price}
              </td>
              <td>
                {p.in_stock === null
                  ? "unknown"
                  : p.in_stock
                  ? p.stock_text || "in stock"
                  : p.stock_text || "out of stock"}
              </td>
              <td>{p.strategy}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
