import React from "react";
import { PipelineState } from "../../types/pipeline";

export const UniverseView: React.FC<{ state: PipelineState }> = ({ state }) => {
  const universeCount = state.universe_size || 318;

  return (
    <div className="workspace-card">
      <div className="workspace-title-section">
        <h2 className="workspace-title">1. Asset Universe & Market Data Feed</h2>
        <div className="workspace-description">
          Base asset universe of {universeCount} liquid US equities, high-beta momentum leaders, and sector/leveraged ETFs.
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: "16px", marginBottom: "24px" }}>
        <div className="m3-card m3-arrive" style={{ marginBottom: 0, padding: "18px" }}>
          <div className="m3-metric-name">Universe Breadth</div>
          <div className="m3-metric-val tabular-nums" style={{ fontSize: "24px", fontWeight: 700, marginTop: "6px", color: "var(--md-sys-color-primary)" }}>
            {universeCount} Symbols
          </div>
          <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginTop: "4px" }}>
            US Equities & High-Volume ETFs
          </div>
        </div>

        <div className="m3-card m3-arrive-delay-1" style={{ marginBottom: 0, padding: "18px" }}>
          <div className="m3-metric-name">Liquidity Filter</div>
          <div className="m3-metric-val tabular-nums" style={{ fontSize: "24px", fontWeight: 700, marginTop: "6px" }}>
            &gt; $10M / day
          </div>
          <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginTop: "4px" }}>
            Median 30-day dollar volume
          </div>
        </div>

        <div className="m3-card m3-arrive-delay-2" style={{ marginBottom: 0, padding: "18px" }}>
          <div className="m3-metric-name">Options Availability</div>
          <div className="m3-metric-val" style={{ fontSize: "24px", fontWeight: 700, marginTop: "6px" }}>
            Penny Pilot
          </div>
          <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginTop: "4px" }}>
            Narrow bid-ask spread requirements
          </div>
        </div>

        <div className="m3-card m3-arrive-delay-3" style={{ marginBottom: 0, padding: "18px" }}>
          <div className="m3-metric-name">Alpaca Market Data</div>
          <div className="m3-metric-val positive" style={{ fontSize: "24px", fontWeight: 700, marginTop: "6px" }}>
            100% Realtime
          </div>
          <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginTop: "4px" }}>
            Live SIP / IEX consolidated bars
          </div>
        </div>
      </div>

      <div className="m3-card m3-arrive-delay-4" style={{ marginBottom: 0 }}>
        <div style={{ fontWeight: 600, fontSize: "14px", marginBottom: "10px", color: "var(--md-sys-color-on-surface)" }}>
          Universe Filtering & Dynamic Ingestion Rules
        </div>
        <ul style={{ paddingLeft: "20px", fontSize: "13px", color: "var(--md-sys-color-on-surface-variant)", lineHeight: 1.8 }}>
          <li>
            <strong>Liquidity and Spread Gate</strong>: Contracts must belong to the Penny Interval Program with tight bid-ask spreads to avoid slippage on fills.
          </li>
          <li>
            <strong>Cross-Sector Representation</strong>: Covers enterprise software (CRM, NOW), semiconductor design (SNPS), cloud & cybersecurity (MDB, ZS), crypto beta (MSTR, COIN), energy & materials (SLB, FCX, NEM), and consumer staples (GIS).
          </li>
          <li>
            <strong>Continuous Polling</strong>: Processed synchronously into the multi-factor quantitative scanner during every pipeline cycle.
          </li>
        </ul>
      </div>
    </div>
  );
};
