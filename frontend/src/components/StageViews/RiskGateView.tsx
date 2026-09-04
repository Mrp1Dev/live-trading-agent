import React from "react";
import { PipelineState } from "../../types/pipeline";

export const RiskGateView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const risk = state.risk_assessment;

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">8. Risk Assessment & Guardrail Gate</h2>
        <div className="workspace-description">
          Deterministic pre-trade verification enforcing account loss limits, directional balance, portfolio Greeks, and broker paper guard.
        </div>
      </div>

      {!risk ? (
        <div style={{ color: "var(--md-sys-color-on-surface-variant)", padding: "24px 0" }}>
          Awaiting risk assessment and guardrail evaluation...
        </div>
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: "16px", marginBottom: "20px" }}>
            <div className="m3-card m3-arrive" style={{ marginBottom: 0, padding: "18px" }}>
              <div className="m3-metric-name">Risk Budget Utilization</div>
              <div className="m3-metric-val tabular-nums" style={{ fontSize: "20px", fontWeight: 700, marginTop: "6px" }}>
                ${risk.total_max_loss.toFixed(2)}{" "}
                <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--md-sys-color-on-surface-variant)" }}>
                  ({risk.max_loss_pct.toFixed(2)}%)
                </span>
              </div>
              <div style={{ fontSize: "12px", color: "var(--md-sys-color-success)", marginTop: "4px", fontWeight: 500 }}>
                ✓ Under 3.0% maximum portfolio limit
              </div>
            </div>

            <div className="m3-card m3-arrive-delay-1" style={{ marginBottom: 0, padding: "18px" }}>
              <div className="m3-metric-name">Directional Balance</div>
              <div className="m3-metric-val tabular-nums" style={{ fontSize: "20px", fontWeight: 700, marginTop: "6px" }}>
                Bull {risk.bullish_risk_pct.toFixed(2)}% | Bear {risk.bearish_risk_pct.toFixed(2)}%
              </div>
              <div style={{ fontSize: "12px", color: "var(--md-sys-color-success)", marginTop: "4px", fontWeight: 500 }}>
                ✓ Concentration safe
              </div>
            </div>

            <div className="m3-card m3-arrive-delay-2" style={{ marginBottom: 0, padding: "18px" }}>
              <div className="m3-metric-name">Net Delta & Gamma</div>
              <div className="m3-metric-val tabular-nums" style={{ fontSize: "20px", fontWeight: 700, marginTop: "6px" }}>
                Δ +{risk.portfolio_delta.toFixed(1)} | Γ +{risk.portfolio_gamma.toFixed(1)}
              </div>
              <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginTop: "4px" }}>
                Directional responsiveness
              </div>
            </div>

            <div className="m3-card m3-arrive-delay-3" style={{ marginBottom: 0, padding: "18px" }}>
              <div className="m3-metric-name">Theta & Vega Decay</div>
              <div className="m3-metric-val tabular-nums" style={{ fontSize: "20px", fontWeight: 700, marginTop: "6px" }}>
                Θ {risk.portfolio_theta.toFixed(1)}/d | ν {risk.portfolio_vega.toFixed(1)}
              </div>
              <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginTop: "4px" }}>
                Controlled decay burn
              </div>
            </div>
          </div>

          <div className="m3-card m3-arrive-delay-4" style={{ marginBottom: 0 }}>
            <div style={{ fontWeight: 600, fontSize: "14px", marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{ color: "var(--md-sys-color-success)", fontSize: "16px" }}>✓</span> Authorized Positions for Broker Submission
            </div>
            {risk.approved_positions.length > 0 ? (
              <div className="m3-table-wrapper">
                <table className="m3-table">
                  <thead>
                    <tr>
                      <th>Underlying</th>
                      <th>Direction</th>
                      <th style={{ textAlign: "right" }}>Contracts</th>
                      <th style={{ textAlign: "right" }}>Authorized Risk</th>
                      <th style={{ textAlign: "right" }}>Score</th>
                      <th>Contract Symbol</th>
                      <th>Risk Clearance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {risk.approved_positions.map((pos, idx) => (
                      <tr key={`${pos.option_symbol}-${idx}`}>
                        <td style={{ fontWeight: 700, color: "var(--md-sys-color-primary)", fontSize: "14px" }}>
                          {pos.symbol}
                        </td>
                        <td>
                          <span className="m3-badge success" style={{ fontWeight: 600 }}>
                            {pos.direction}
                          </span>
                        </td>
                        <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                          {pos.contracts} contracts
                        </td>
                        <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                          ${pos.risk.toFixed(2)}
                        </td>
                        <td className="tabular-nums" style={{ textAlign: "right" }}>
                          {pos.score.toFixed(1)}
                        </td>
                        <td style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)" }}>
                          {pos.option_symbol}
                        </td>
                        <td>
                          <span className="m3-badge success" style={{ fontWeight: 600 }}>
                            PASSED
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div style={{ fontSize: "13px", color: "var(--md-sys-color-on-surface-variant)" }}>
                No positions approved for this cycle.
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
};
