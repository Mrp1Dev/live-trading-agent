"use client";

import React, { useEffect, useState } from "react";
import { AccountState, PipelineState } from "../types/pipeline";

interface LeftRailProps {
  account: AccountState;
  onConnect: (apiKey: string, secretKey: string) => Promise<boolean>;
  onDisconnect: () => void;
  onLaunchAgent: () => void;
  onStopAgent: () => void;
  isRunning: boolean;
  pipelineState: PipelineState;
}

export const LeftRail: React.FC<LeftRailProps> = ({
  account,
  onConnect,
  onDisconnect,
  onLaunchAgent,
  onStopAgent,
  isRunning,
  pipelineState,
}) => {
  const [apiKey, setApiKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const handleSubmitConnect = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!apiKey.trim() || !secretKey.trim()) {
      setErrorMsg("Please enter both Alpaca API Key and Secret Key.");
      return;
    }
    setErrorMsg("");
    setConnecting(true);
    try {
      const ok = await onConnect(apiKey.trim(), secretKey.trim());
      if (!ok) {
        setErrorMsg("Failed to connect to Alpaca Paper API. Please verify keys.");
      }
    } catch {
      setErrorMsg("Error connecting to Alpaca.");
    } finally {
      setConnecting(false);
    }
  };

  const accountId = account.account_id || pipelineState.account_id;
  const maskedId = accountId
    ? `${accountId.slice(0, 8)}...${accountId.slice(-4)}`
    : "Not Connected";

  const equityDisplay = (account.equity > 0 ? account.equity : pipelineState.equity).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });

  const buyingPowerDisplay = (account.buying_power > 0 ? account.buying_power : pipelineState.buying_power).toLocaleString(
    "en-US",
    { style: "currency", currency: "USD" }
  );

  const cashDisplay = (account.cash > 0 ? account.cash : pipelineState.remaining_capital).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
  });

  const dayPnlDisplay = `${account.day_pnl >= 0 ? "+" : ""}$${account.day_pnl.toFixed(2)} (${account.day_pnl_pct >= 0 ? "+" : ""}${account.day_pnl_pct.toFixed(2)}%)`;

  const slotsUsed = pipelineState.active_positions.length;

  return (
    <aside style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* 1. Alpaca Paper Credentials Card */}
      <div className="m3-card" style={{ marginBottom: 0 }}>
        <div className="m3-card-header">
          <span className="m3-card-title">🔑 Alpaca Paper Account</span>
          {account.is_connected ? (
            <span className="m3-badge success" style={{ fontSize: "11px", fontWeight: 600 }}>
              CONNECTED
            </span>
          ) : (
            <span className="m3-badge neutral" style={{ fontSize: "11px", fontWeight: 600 }}>
              PAPER MODE
            </span>
          )}
        </div>

        {account.is_connected ? (
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
              <span className="pulse-dot" />
              <span style={{ color: "var(--md-sys-color-success)", fontSize: "13px", fontWeight: 600 }}>
                Broker Authenticated
              </span>
            </div>

            <div
              style={{
                backgroundColor: "var(--md-sys-color-surface-container)",
                borderRadius: "var(--md-shape-md)",
                padding: "10px 12px",
                marginBottom: "14px",
                border: "1px solid var(--md-sys-color-outline-variant)",
              }}
            >
              <div style={{ fontSize: "11px", color: "var(--md-sys-color-on-surface-variant)", textTransform: "uppercase", fontWeight: 600, marginBottom: "2px" }}>
                Account ID
              </div>
              <div style={{ fontFamily: "var(--md-sys-font-mono)", fontSize: "12px", color: "var(--md-sys-color-on-surface)", fontWeight: 600 }}>
                {accountId || maskedId}
              </div>
            </div>

            <button
              type="button"
              className="btn-m3-tonal btn-m3-block"
              onClick={onDisconnect}
              disabled={isRunning}
              style={{ height: "36px", fontSize: "12px" }}
            >
              Disconnect Account
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmitConnect}>
            <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", marginBottom: "14px", lineHeight: 1.5 }}>
              Enter your Alpaca Paper API keys to authenticate and authorize live order execution.
            </div>

            <div className="m3-text-field">
              <label className="m3-text-label">Alpaca API Key ID</label>
              <input
                type="text"
                className="m3-text-input"
                placeholder="e.g. PK..."
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                autoComplete="off"
                spellCheck="false"
              />
            </div>

            <div className="m3-text-field">
              <label className="m3-text-label">Alpaca Secret Key</label>
              <input
                type="password"
                className="m3-text-input"
                placeholder="••••••••••••••••"
                value={secretKey}
                onChange={(e) => setSecretKey(e.target.value)}
                autoComplete="off"
                spellCheck="false"
              />
            </div>

            {errorMsg && (
              <div style={{ fontSize: "12px", color: "var(--md-sys-color-error)", marginBottom: "12px", fontWeight: 500 }}>
                {errorMsg}
              </div>
            )}

            <button
              type="submit"
              className="btn-m3-filled btn-m3-block"
              disabled={!mounted || connecting}
              suppressHydrationWarning
              style={{ height: "42px", marginTop: "4px" }}
            >
              {connecting ? "Authenticating..." : "🔑 Connect Paper Account"}
            </button>
          </form>
        )}
      </div>

      {/* 2. Autonomous Trading Engine Control Card */}
      <div className="m3-card" style={{ marginBottom: 0 }}>
        <div className="m3-card-header" style={{ marginBottom: "12px" }}>
          <span className="m3-card-title">⚡ Trading Engine</span>
          {isRunning ? (
            <span className="m3-badge success" style={{ fontSize: "11px", fontWeight: 600 }}>
              ● RUNNING
            </span>
          ) : (
            <span className="m3-badge neutral" style={{ fontSize: "11px", fontWeight: 600 }}>
              STANDBY
            </span>
          )}
        </div>

        {!isRunning ? (
          <div>
            <button
              type="button"
              className="btn-m3-filled btn-m3-block"
              onClick={onLaunchAgent}
              disabled={!mounted || !account.is_connected}
              suppressHydrationWarning
              title={!account.is_connected ? "Connect your Alpaca paper keys first" : "Start or resume autonomous trading engine"}
              style={{
                height: "46px",
                fontSize: "14px",
                opacity: !account.is_connected ? 0.6 : 1,
              }}
            >
              ⚡ Launch Autonomous Agent
            </button>
            <div style={{ fontSize: "11px", color: "var(--md-sys-color-on-surface-variant)", textAlign: "center", marginTop: "8px", lineHeight: 1.4 }}>
              {account.is_connected ? (
                <>Executes: <code>python main.py --confirm-paper-trades</code></>
              ) : (
                <>Connect Alpaca keys above to enable execution</>
              )}
            </div>
          </div>
        ) : (
          <div>
            <button
              type="button"
              className="btn-m3-filled btn-m3-block danger"
              onClick={onStopAgent}
              style={{ height: "46px", fontSize: "14px" }}
            >
              🛑 Stop Agent (Ctrl+C)
            </button>
            <div style={{ fontSize: "11px", color: "var(--md-sys-color-on-surface-variant)", textAlign: "center", marginTop: "8px", lineHeight: 1.4 }}>
              Clean interrupt • Positions left intact • Ready to resume
            </div>
          </div>
        )}
      </div>

      {/* 3. Account Overview Card */}
      <div className="m3-card" style={{ marginBottom: 0 }}>
        <div className="m3-card-header">
          <span className="m3-card-title">📊 Account Financials</span>
          <span className="m3-badge neutral" style={{ fontSize: "11px", fontWeight: 600 }}>PAPER TRADING</span>
        </div>

        <div className="m3-metric-list">
          <div className="m3-metric-item">
            <span className="m3-metric-name">Account Equity</span>
            <span className="m3-metric-val tabular-nums">{equityDisplay}</span>
          </div>

          <div className="m3-metric-item">
            <span className="m3-metric-name">Today's P&L</span>
            <span className={`m3-metric-val tabular-nums ${account.day_pnl >= 0 ? "positive" : "negative"}`}>
              {dayPnlDisplay}
            </span>
          </div>

          <div className="m3-metric-item">
            <span className="m3-metric-name">Buying Power</span>
            <span className="m3-metric-val tabular-nums">{buyingPowerDisplay}</span>
          </div>

          <div className="m3-metric-item">
            <span className="m3-metric-name">Cash Balance</span>
            <span className="m3-metric-val tabular-nums">{cashDisplay}</span>
          </div>

          <div className="m3-metric-item">
            <span className="m3-metric-name">Filled Positions</span>
            <span className="m3-metric-val tabular-nums" style={{ color: "var(--md-sys-color-primary)", fontWeight: 700 }}>
              {slotsUsed} / 8 slots
            </span>
          </div>
        </div>
      </div>

      {/* 4. Strategy Cadence Card */}
      <div className="m3-card" style={{ marginBottom: 0 }}>
        <div className="m3-card-header">
          <span className="m3-card-title">⚙️ Strategy Cadence</span>
          <span className="m3-badge success" style={{ fontSize: "11px" }}>ACTIVE</span>
        </div>
        <div style={{ fontSize: "12px", color: "var(--md-sys-color-on-surface-variant)", lineHeight: 1.6 }}>
          • <strong>Universe</strong>: 318 liquid US equities & ETFs<br />
          • <strong>Entries</strong>: Every 15 min when slots are free<br />
          • <strong>Exits</strong>: Continuous trailing stop monitoring<br />
          • <strong>Mode</strong>: Autonomous paper order execution
        </div>
      </div>
    </aside>
  );
};
