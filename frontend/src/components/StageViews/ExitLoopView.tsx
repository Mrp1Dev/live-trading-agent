import React from "react";
import { PipelineState } from "../../types/pipeline";

export const ExitLoopView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const positions = state.active_positions;
  const pendingOrders = state.execution_items.filter((item) => item.status === "SUBMITTED" && item.filled_qty === 0);

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">10. Active Positions & Autonomous Exit Loop</h2>
        <div className="workspace-description">
          Continuous mark-to-market position monitoring against live broker bids. Enforces automated trailing stops, profit targets, and hard stop losses.
        </div>
      </div>

      {positions.length === 0 ? (
        <div className="m3-card" style={{ padding: "28px", textAlign: "center", marginBottom: "20px" }}>
          <div style={{ fontWeight: 600, fontSize: "15px", marginBottom: "6px" }}>
            {pendingOrders.length > 0 ? "Orders Submitted • Awaiting Broker Fill" : "No Open Positions Held"}
          </div>
          <div style={{ fontSize: "13px", color: "var(--md-sys-color-on-surface-variant)", maxWidth: "600px", margin: "0 auto", lineHeight: 1.6 }}>
            {pendingOrders.length > 0 ? (
              <>
                {pendingOrders.length} order{pendingOrders.length === 1 ? "" : "s"} submitted to Alpaca paper account (
                {pendingOrders.map((o) => o.symbol).join(", ")}). Positions will be tracked here in real-time as broker fills confirm.
              </>
            ) : (
              "All 8 portfolio slots are free. The agent evaluates new entries every cycle during active market hours."
            )}
          </div>
        </div>
      ) : (
        <div className="m3-table-wrapper" style={{ marginBottom: "20px" }}>
          <table className="m3-table">
            <thead>
              <tr>
                <th>Contract Symbol</th>
                <th>Underlying</th>
                <th>Direction</th>
                <th style={{ textAlign: "right" }}>Qty</th>
                <th style={{ textAlign: "right" }}>Entry Price</th>
                <th style={{ textAlign: "right" }}>Current Bid</th>
                <th style={{ textAlign: "right" }}>Cost Basis</th>
                <th style={{ textAlign: "right" }}>Market Value</th>
                <th style={{ textAlign: "right" }}>Unrealized P&L</th>
                <th style={{ textAlign: "right" }}>Peak Profit</th>
                <th style={{ textAlign: "right" }}>DTE</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p, index) => {
                const isProfitable = p.unrealized_pnl >= 0;
                return (
                  <tr
                    key={`${p.symbol}-${index}`}
                    className="m3-arrive"
                    style={{ animationDelay: `${index * 40}ms` }}
                  >
                    <td style={{ fontWeight: 700, color: "var(--md-sys-color-primary)", fontSize: "13px" }}>
                      {p.symbol}
                    </td>
                    <td style={{ fontWeight: 600 }}>{p.underlying}</td>
                    <td>
                      <span className="m3-badge success" style={{ fontWeight: 600 }}>
                        {p.direction}
                      </span>
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>{p.qty}</td>
                    <td className="tabular-nums" style={{ textAlign: "right" }}>${p.entry_price.toFixed(2)}</td>
                    <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>${p.current_price.toFixed(2)}</td>
                    <td className="tabular-nums" style={{ textAlign: "right" }}>${p.cost_basis.toFixed(2)}</td>
                    <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>${p.market_value.toFixed(2)}</td>
                    <td className={`tabular-nums ${isProfitable ? "positive" : "negative"}`} style={{ textAlign: "right", fontWeight: 700 }}>
                      {isProfitable ? "+" : ""}${p.unrealized_pnl.toFixed(2)} ({isProfitable ? "+" : ""}{p.unrealized_pnl_pct.toFixed(2)}%)
                    </td>
                    <td className="tabular-nums positive" style={{ textAlign: "right", fontWeight: 600 }}>+{p.peak_pnl_pct.toFixed(1)}%</td>
                    <td className="tabular-nums" style={{ textAlign: "right" }}>{p.dte !== undefined ? `${p.dte}d` : "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Exit Management Policy */}
      <div className="m3-card" style={{ marginBottom: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "14px", marginBottom: "14px", color: "var(--md-sys-color-on-surface)" }}>
          🛡️ Autonomous Exit Management Rules
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "16px" }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: "13px", color: "var(--md-sys-color-primary)", marginBottom: "4px" }}>
              Trailing Stop (+30% Trigger)
            </div>
            <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", lineHeight: 1.6 }}>
              Arms when contract hits +30% profit. Exits immediately if giving back 35% of peak gains.
            </div>
          </div>

          <div>
            <div style={{ fontWeight: 600, fontSize: "13px", color: "var(--md-sys-color-error)", marginBottom: "4px" }}>
              Hard Stop Loss (-55%)
            </div>
            <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", lineHeight: 1.6 }}>
              Strict catastrophic stop loss capped at -55% of entry premium to protect capital.
            </div>
          </div>

          <div>
            <div style={{ fontWeight: 600, fontSize: "13px", color: "var(--md-sys-color-warning)", marginBottom: "4px" }}>
              Expiration Stop (1 DTE)
            </div>
            <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", lineHeight: 1.6 }}>
              Force liquidation 24 hours prior to expiration to eliminate pin and assignment risk.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
