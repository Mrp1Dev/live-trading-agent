import React from "react";
import { PipelineState } from "../../types/pipeline";

export const ExecutionView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const items = state.execution_items;

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">9. Alpaca Paper Trade Execution Report</h2>
        <div className="workspace-description">
          Exact order submissions dispatched to Alpaca Paper Trading API with limit prices, broker order IDs, and real-time fill confirmations.
        </div>
      </div>

      {items.length === 0 ? (
        <div style={{ color: "var(--md-sys-color-on-surface-variant)", padding: "24px 0" }}>
          Awaiting order submissions from execution engine...
        </div>
      ) : (
        <div className="m3-table-wrapper">
          <table className="m3-table">
            <thead>
              <tr>
                <th>Contract Symbol</th>
                <th>Action</th>
                <th style={{ textAlign: "right" }}>Requested</th>
                <th style={{ textAlign: "right" }}>Submitted</th>
                <th style={{ textAlign: "right" }}>Filled</th>
                <th style={{ textAlign: "right" }}>Limit Price</th>
                <th>Order Status</th>
                <th>Broker Order ID</th>
                <th>Execution Response</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, idx) => (
                <tr
                  key={`${item.symbol}-${item.order_id || idx}`}
                  className="m3-arrive"
                  style={{ animationDelay: `${idx * 40}ms` }}
                >
                  <td style={{ fontWeight: 700, color: "var(--md-sys-color-primary)", fontSize: "13px" }}>
                    {item.symbol}
                  </td>
                  <td style={{ fontWeight: 600 }}>{item.action}</td>
                  <td className="tabular-nums" style={{ textAlign: "right" }}>{item.requested_qty}</td>
                  <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>{item.submitted_qty}</td>
                  <td className="tabular-nums" style={{ textAlign: "right" }}>{item.filled_qty}</td>
                  <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                    ${item.limit_price.toFixed(2)}
                  </td>
                  <td>
                    <span className="m3-badge success" style={{ fontWeight: 600 }}>
                      ● {item.status}
                    </span>
                  </td>
                  <td style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)" }}>
                    {item.order_id ? `${item.order_id.slice(0, 8)}...` : "-"}
                  </td>
                  <td style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)" }}>
                    {item.result}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
