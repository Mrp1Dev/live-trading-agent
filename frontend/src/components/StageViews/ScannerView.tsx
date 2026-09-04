import React from "react";
import { PipelineState } from "../../types/pipeline";

export const ScannerView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const picks = state.scanner_picks;

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">2. Quantitative Stock Scanner (Top 20 Picks)</h2>
        <div className="workspace-description">
          Cross-sectional momentum, volume anomalies, trend distance, and relative strength vs SPY across the universe.
        </div>
      </div>

      {picks.length === 0 ? (
        <div style={{ color: "var(--md-sys-color-on-surface-variant)", padding: "24px 0" }}>
          Awaiting scanner execution from the autonomous pipeline...
        </div>
      ) : (
        <div className="m3-table-wrapper">
          <table className="m3-table">
            <thead>
              <tr>
                <th style={{ width: "60px" }}>Rank</th>
                <th>Symbol</th>
                <th style={{ textAlign: "right" }}>Price</th>
                <th style={{ textAlign: "right" }}>1D Return</th>
                <th style={{ textAlign: "right" }}>5D Return</th>
                <th style={{ textAlign: "right" }}>20D Return</th>
                <th style={{ textAlign: "right" }}>Vol / Avg</th>
                <th style={{ textAlign: "right" }}>Realized Vol</th>
                <th style={{ textAlign: "right" }}>RS vs SPY</th>
                <th style={{ textAlign: "right" }}>Composite Score</th>
              </tr>
            </thead>
            <tbody>
              {picks.map((p, idx) => {
                const isPos1D = p.ret_1d.startsWith("+");
                const isPos5D = p.ret_5d.startsWith("+");
                const isPos20D = p.ret_20d.startsWith("+");

                return (
                  <tr
                    key={`${p.symbol}-${p.rank}-${idx}`}
                    className="m3-arrive"
                    style={{ animationDelay: `${Math.min(idx * 25, 400)}ms` }}
                  >
                    <td style={{ fontWeight: 600, color: "var(--md-sys-color-on-surface-variant)" }}>
                      #{p.rank}
                    </td>
                    <td style={{ fontWeight: 700, color: "var(--md-sys-color-primary)", fontSize: "14px" }}>
                      {p.symbol}
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                      ${p.price.toFixed(2)}
                    </td>
                    <td className={`tabular-nums ${isPos1D ? "positive" : "negative"}`} style={{ textAlign: "right", fontWeight: 500 }}>
                      {p.ret_1d}
                    </td>
                    <td className={`tabular-nums ${isPos5D ? "positive" : "negative"}`} style={{ textAlign: "right", fontWeight: 500 }}>
                      {p.ret_5d}
                    </td>
                    <td className={`tabular-nums ${isPos20D ? "positive" : "negative"}`} style={{ textAlign: "right", fontWeight: 600 }}>
                      {p.ret_20d}
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right" }}>
                      {p.vol_ratio}
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right" }}>
                      {p.realized_vol}
                    </td>
                    <td className="tabular-nums positive" style={{ textAlign: "right", fontWeight: 600 }}>
                      {p.rs_vs_spy}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <span className="m3-badge success tabular-nums" style={{ fontWeight: 600 }}>
                        {p.score.toFixed(1)}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};
