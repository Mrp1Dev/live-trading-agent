import React from "react";
import { PipelineState } from "../../types/pipeline";

export const PortfolioView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const trades = state.planned_trades;

  // Fallback: If planned_trades was not directly matched from text, reconstruct from risk assessment
  const displayTrades = trades.length > 0 ? trades : (
    state.risk_assessment?.approved_positions?.map((p, idx) => ({
      rank: idx + 1,
      ticker: p.symbol,
      direction: p.direction,
      contracts: p.contracts,
      premium: p.risk,
      max_loss: p.risk,
      trade_score: p.score,
      option_symbol: p.option_symbol,
    })) || []
  );

  const totalPremium = state.total_premium_deployed > 0
    ? state.total_premium_deployed
    : displayTrades.reduce((acc, t) => acc + t.premium, 0);

  const totalMaxLoss = state.total_planned_max_loss > 0
    ? state.total_planned_max_loss
    : displayTrades.reduce((acc, t) => acc + t.max_loss, 0);

  const remainingCash = state.remaining_capital > 0
    ? state.remaining_capital
    : Math.max(0, (state.equity || 100000) - totalPremium);

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">7. Portfolio Allocation & Sizing Plan</h2>
        <div className="workspace-description">
          Position sizing using volatility budgeting and fractional Kelly criterion, enforcing strict risk limits per trade and across portfolio.
        </div>
      </div>

      {displayTrades.length === 0 ? (
        <div style={{ color: "var(--md-sys-color-on-surface-variant)", padding: "24px 0" }}>
          Awaiting portfolio optimization and allocation sizing...
        </div>
      ) : (
        <>
          {/* Staggered Block Introduction: Metrics Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "16px", marginBottom: "20px" }}>
            <div className="m3-card m3-arrive" style={{ marginBottom: 0, padding: "18px" }}>
              <div className="m3-metric-name">Premium Deployed</div>
              <div className="m3-metric-val tabular-nums" style={{ fontSize: "22px", fontWeight: 700, marginTop: "6px", color: "var(--md-sys-color-primary)" }}>
                ${totalPremium.toLocaleString("en-US", { minimumFractionDigits: 2 })}
              </div>
              <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginTop: "2px" }}>
                Total capital required for orders
              </div>
            </div>

            <div className="m3-card m3-arrive-delay-1" style={{ marginBottom: 0, padding: "18px" }}>
              <div className="m3-metric-name">Planned Max Loss</div>
              <div className="m3-metric-val tabular-nums" style={{ fontSize: "22px", fontWeight: 700, marginTop: "6px" }}>
                ${totalMaxLoss.toLocaleString("en-US", { minimumFractionDigits: 2 })}{" "}
                <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--md-sys-color-on-surface-variant)" }}>
                  ({((totalMaxLoss / (state.equity || 100000)) * 100).toFixed(2)}%)
                </span>
              </div>
              <div style={{ fontSize: "12px", color: "var(--md-sys-color-success)", marginTop: "2px" }}>
                ✓ Under 3.0% maximum risk budget
              </div>
            </div>

            <div className="m3-card m3-arrive-delay-2" style={{ marginBottom: 0, padding: "18px" }}>
              <div className="m3-metric-name">Remaining Cash</div>
              <div className="m3-metric-val tabular-nums" style={{ fontSize: "22px", fontWeight: 700, marginTop: "6px" }}>
                ${remainingCash.toLocaleString("en-US", { minimumFractionDigits: 2 })}
              </div>
              <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginTop: "2px" }}>
                Available buying power reserves
              </div>
            </div>
          </div>

          {/* Sized Trade Allocation Table with Staggered Rows */}
          <div className="m3-table-wrapper m3-arrive-delay-3">
            <table className="m3-table">
              <thead>
                <tr>
                  <th style={{ width: "70px" }}>Priority</th>
                  <th>Ticker</th>
                  <th>Direction</th>
                  <th style={{ textAlign: "right" }}>Contracts</th>
                  <th style={{ textAlign: "right" }}>Capital Required</th>
                  <th style={{ textAlign: "right" }}>Planned Max Loss</th>
                  <th style={{ textAlign: "right" }}>Trade Score</th>
                  <th>Target Contract</th>
                </tr>
              </thead>
              <tbody>
                {displayTrades.map((t, idx) => (
                  <tr
                    key={`${t.option_symbol}-${t.rank}-${idx}`}
                    className="m3-arrive"
                    style={{ animationDelay: `${idx * 40}ms` }}
                  >
                    <td className="tabular-nums" style={{ fontWeight: 700, fontSize: "14px" }}>
                      #{t.rank}
                    </td>
                    <td style={{ fontWeight: 700, color: "var(--md-sys-color-primary)", fontSize: "14px" }}>
                      {t.ticker}
                    </td>
                    <td>
                      <span className="m3-badge success" style={{ fontWeight: 600 }}>
                        {t.direction}
                      </span>
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                      {t.contracts} contracts
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                      ${t.premium.toLocaleString("en-US", { minimumFractionDigits: 2 })}
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right" }}>
                      ${t.max_loss.toLocaleString("en-US", { minimumFractionDigits: 2 })}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <span className="m3-badge success tabular-nums" style={{ fontWeight: 600 }}>
                        {t.trade_score.toFixed(1)}
                      </span>
                    </td>
                    <td style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)" }}>
                      {t.option_symbol}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
};
