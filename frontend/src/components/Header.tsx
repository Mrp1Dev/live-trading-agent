"use client";

import React, { useEffect, useState } from "react";

interface HeaderProps {
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onReset: () => void;
  isRunning: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  theme,
  onToggleTheme,
  onReset,
  isRunning,
}) => {
  const [marketTime, setMarketTime] = useState<string>("");
  const [isOpen, setIsOpen] = useState<boolean>(true);

  useEffect(() => {
    const updateClock = () => {
      const now = new Date();
      const options: Intl.DateTimeFormatOptions = {
        timeZone: "America/New_York",
        hour: "numeric",
        minute: "numeric",
        second: "numeric",
        hour12: true,
      };
      const etString = new Intl.DateTimeFormat("en-US", options).format(now);
      setMarketTime(`${etString} ET`);

      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        weekday: "short",
        hour: "numeric",
        minute: "numeric",
        hour12: false,
      }).formatToParts(now);

      let weekday = "";
      let hour = 0;
      let min = 0;
      for (const p of parts) {
        if (p.type === "weekday") weekday = p.value;
        if (p.type === "hour") hour = parseInt(p.value, 10);
        if (p.type === "minute") min = parseInt(p.value, 10);
      }

      const isWeekday = weekday !== "Sat" && weekday !== "Sun";
      const totalMinutes = hour * 60 + min;
      const marketOpen = 9 * 60 + 30;
      const marketClose = 16 * 60;
      setIsOpen(isWeekday && totalMinutes >= marketOpen && totalMinutes < marketClose);
    };

    updateClock();
    const interval = setInterval(updateClock, 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <header className="app-header">
      <div className="brand-section">
        <div className="brand-avatar">⚡</div>
        <div>
          <div className="brand-title">Alpaca Autonomous Agent</div>
          <div className="brand-subtitle">Autonomous Options Trading Engine • Material You</div>
        </div>
      </div>

      <div className="header-actions">
        {marketTime && (
          <div className="market-status-pill">
            <span
              className="pulse-dot"
              style={{ backgroundColor: isOpen ? "var(--md-sys-color-success)" : "var(--md-sys-color-warning)" }}
            />
            <span style={{ color: "var(--md-sys-color-on-surface-variant)" }}>New York:</span>
            <strong className="tabular-nums" style={{ fontWeight: 600 }}>{marketTime}</strong>
            <span
              className={`m3-badge ${isOpen ? "success" : "neutral"}`}
              style={{ padding: "2px 8px", fontSize: "11px" }}
            >
              {isOpen ? "MARKET OPEN" : "MARKET CLOSED"}
            </span>
          </div>
        )}

        <button
          className="btn-m3-tonal"
          onClick={onReset}
          title="Clear logs and reset pipeline state"
        >
          🧹 Reset
        </button>

        <button
          className="btn-m3-icon"
          onClick={onToggleTheme}
          title={`Switch to ${theme === "light" ? "Dark" : "Light"} theme`}
          aria-label="Toggle Theme"
        >
          {theme === "light" ? "🌙" : "☀️"}
        </button>
      </div>
    </header>
  );
};
