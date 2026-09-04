"use client";

import React, { useEffect, useRef, useState } from "react";

interface ExecutionConsoleProps {
  logs: string[];
  isRunning: boolean;
}

export const ExecutionConsole: React.FC<ExecutionConsoleProps> = ({ logs, isRunning }) => {
  const [filterText, setFilterText] = useState("");
  const [lineLimit, setLineLimit] = useState(300);
  const [autoScroll, setAutoScroll] = useState(true);
  const [copied, setCopied] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const filteredLogs = logs
    .filter((line) => !filterText || line.toLowerCase().includes(filterText.toLowerCase()))
    .slice(-lineLimit);

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filteredLogs, autoScroll]);

  const handleCopy = () => {
    navigator.clipboard.writeText(logs.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const getLineClass = (line: string) => {
    if (line.startsWith("> python")) return "term-cmd";
    if (line.includes("ET]") || line.includes("equity $") || line.includes("Account ID:")) return "term-meta";
    if (line.includes("===") || line.includes("---")) return "term-header";
    if (
      line.toLowerCase().includes("filled") ||
      line.toLowerCase().includes("submitted") ||
      line.toLowerCase().includes("connected to alpaca") ||
      line.toLowerCase().includes("approved")
    ) {
      return "term-success";
    }
    if (line.toLowerCase().includes("skipping") || line.toLowerCase().includes("veto") || line.toLowerCase().includes("warning")) {
      return "term-warn";
    }
    if (line.toLowerCase().includes("error") || line.toLowerCase().includes("failed") || line.toLowerCase().includes("rejected")) {
      return "term-err";
    }
    return "";
  };

  return (
    <div className="terminal-box">
      <div className="terminal-header">
        <div className="terminal-title">
          <span>🖥️ Real-Time Execution Console</span>
          <span className={`m3-badge ${isRunning ? "success" : "neutral"}`} style={{ fontSize: "11px", fontWeight: 600 }}>
            ● {isRunning ? "LIVE STREAMING" : "STREAM IDLE"}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <input
            type="text"
            className="m3-text-input"
            placeholder="Filter stream (e.g. CRM, NOW)..."
            style={{
              width: "220px",
              height: "32px",
              padding: "0 10px",
              fontSize: "12px",
              borderRadius: "9999px",
              backgroundColor: "var(--term-surface)",
              color: "var(--term-fg)",
              borderColor: "var(--term-border)",
            }}
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
          />

          <select
            value={lineLimit}
            onChange={(e) => setLineLimit(Number(e.target.value))}
            style={{
              height: "32px",
              padding: "0 10px",
              fontSize: "12px",
              borderRadius: "9999px",
              backgroundColor: "var(--term-surface)",
              color: "var(--term-fg)",
              border: "1px solid var(--term-border)",
              cursor: "pointer",
            }}
          >
            <option value={100}>100 lines</option>
            <option value={300}>300 lines</option>
            <option value={500}>500 lines</option>
            <option value={1000}>1000 lines</option>
          </select>

          <button
            type="button"
            className="btn-m3-tonal"
            style={{
              height: "32px",
              padding: "0 12px",
              fontSize: "12px",
              backgroundColor: "var(--term-surface)",
              color: "var(--term-fg)",
            }}
            onClick={() => setAutoScroll(!autoScroll)}
          >
            {autoScroll ? "Auto-Scroll: ON" : "Auto-Scroll: OFF"}
          </button>

          <button
            type="button"
            className="btn-m3-tonal"
            style={{
              height: "32px",
              padding: "0 12px",
              fontSize: "12px",
              backgroundColor: "var(--term-surface)",
              color: "var(--term-fg)",
            }}
            onClick={handleCopy}
          >
            {copied ? "✓ Copied" : "Copy"}
          </button>
        </div>
      </div>

      <div className="terminal-stream code-mono" ref={scrollRef}>
        {filteredLogs.length === 0 ? (
          <div style={{ color: "#8b949e", padding: "14px 0", lineHeight: 1.8 }}>
            &gt; python main.py --confirm-paper-trades
            <br />
            [Console stream idle. Click &quot;⚡ Launch Autonomous Agent&quot; on the left or &quot;📁 Load Demo&quot; above to view live stream logs.]
          </div>
        ) : (
          filteredLogs.map((line, idx) => (
            <div key={idx} className={`terminal-line ${getLineClass(line)}`}>
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
};
