import React from "react";
import { PipelineState } from "../../types/pipeline";

export const StockRankView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const ranks = state.llm_rankings;
  const skipped = state.skipped_symbols;

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">4. DeepSeek LLM Stock Ranker</h2>
        <div className="workspace-description">
          Synthesizes quantitative momentum metrics with qualitative news and catalyst timing to prioritize directional entry candidates.
        </div>
      </div>

      {ranks.length === 0 ? (
        <div style={{ color: "var(--md-sys-color-on-surface-variant)", padding: "24px 0" }}>
          Awaiting LLM Stock Ranker evaluations...
        </div>
      ) : (
        <div className="m3-table-wrapper">
          <table className="m3-table">
            <thead>
              <tr>
                <th style={{ width: "80px" }}>LLM Rank</th>
                <th>Symbol</th>
                <th style={{ textAlign: "right" }}>Scanner Rank</th>
                <th style={{ textAlign: "right" }}>Scanner Score</th>
                <th>Options Liquidity / Filter Status</th>
              </tr>
            </thead>
            <tbody>
              {ranks.map((r, index) => {
                const isSkipped = !!skipped[r.symbol];
                const skipReason = skipped[r.symbol];

                return (
                  <tr
                    key={`${r.symbol}-${r.rank}-${index}`}
                    className="m3-arrive"
                    style={{ animationDelay: `${index * 35}ms` }}
                  >
                    <td className="tabular-nums" style={{ fontWeight: 700, fontSize: "14px" }}>
                      #{r.rank}
                    </td>
                    <td style={{ fontWeight: 700, color: "var(--md-sys-color-primary)", fontSize: "14px" }}>
                      {r.symbol}
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right" }}>
                      {r.scanner_rank}
                    </td>
                    <td className="tabular-nums" style={{ textAlign: "right", fontWeight: 600 }}>
                      {r.scanner_score.toFixed(1)}
                    </td>
                    <td>
                      {isSkipped ? (
                        <span className="m3-badge warning" style={{ fontWeight: 500 }}>
                          ⚠️ Skipped ({skipReason})
                        </span>
                      ) : (
                        <span className="m3-badge success" style={{ fontWeight: 500 }}>
                          ✓ Options Survived Filter
                        </span>
                      )}
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
