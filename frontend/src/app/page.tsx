"use client";

import React, { useEffect, useRef, useState } from "react";
import { Header } from "../components/Header";
import { LeftRail } from "../components/LeftRail";
import { PipelineStepper } from "../components/PipelineStepper";
import { ExecutionConsole } from "../components/ExecutionConsole";

import { UniverseView } from "../components/StageViews/UniverseView";
import { ScannerView } from "../components/StageViews/ScannerView";
import { ResearchView } from "../components/StageViews/ResearchView";
import { StockRankView } from "../components/StageViews/StockRankView";
import { OptionsChainView } from "../components/StageViews/OptionsChainView";
import { GlobalRankView } from "../components/StageViews/GlobalRankView";
import { PortfolioView } from "../components/StageViews/PortfolioView";
import { RiskGateView } from "../components/StageViews/RiskGateView";
import { ExecutionView } from "../components/StageViews/ExecutionView";
import { ExitLoopView } from "../components/StageViews/ExitLoopView";

import { AccountState, LivePosition, PipelineState } from "../types/pipeline";
import { createInitialState, parseConsoleText } from "../lib/parser";

export default function DashboardPage() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [pipelineState, setPipelineState] = useState<PipelineState>(createInitialState);
  const [logs, setLogs] = useState<string[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [selectedStageIndex, setSelectedStageIndex] = useState(0);
  const [followLive, setFollowLive] = useState(true);

  const [connectedKeys, setConnectedKeys] = useState<{ apiKey: string; secretKey: string } | null>(null);

  const [account, setAccount] = useState<AccountState>({
    account_id: "",
    status: "",
    equity: 100000.0,
    cash: 100000.0,
    buying_power: 400000.0,
    day_pnl: 0.0,
    day_pnl_pct: 0.0,
    is_connected: false,
  });

  const eventSourceRef = useRef<EventSource | null>(null);
  const accumulatedLogsRef = useRef<string>("");
  const followLiveRef = useRef(followLive);
  followLiveRef.current = followLive;

  // Apply theme to document
  const toggleTheme = () => {
    const next = theme === "light" ? "dark" : "light";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
  };

  // Fetch true live positions and balances from Alpaca
  const syncAlpacaAccountAndPositions = async (apiKey: string, secretKey: string) => {
    try {
      // 1. Sync Account
      const accRes = await fetch("/api/alpaca/account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ apiKey, secretKey }),
      });
      if (accRes.ok) {
        const accData = await accRes.json();
        setAccount({
          account_id: accData.account_id,
          status: accData.status,
          equity: accData.equity,
          cash: accData.cash,
          buying_power: accData.buying_power,
          day_pnl: accData.day_pnl,
          day_pnl_pct: accData.day_pnl_pct,
          is_connected: true,
        });
      }

      // 2. Sync Real Positions (NO made up data)
      const posRes = await fetch("/api/alpaca/positions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ apiKey, secretKey }),
      });
      if (posRes.ok) {
        const posData = await posRes.json();
        if (Array.isArray(posData)) {
          const mappedPositions: LivePosition[] = posData.map((p: any) => {
            const sym = p.symbol || "";
            const isCall = sym.includes("C00");
            const symPrefix = sym.includes("26") ? sym.slice(0, sym.indexOf("26")) : sym;
            const pnlPct = parseFloat(p.unrealized_plpc || "0") * 100;
            return {
              symbol: sym,
              underlying: symPrefix,
              direction: isCall ? "BULLISH" : "BEARISH",
              qty: Math.abs(parseInt(p.qty, 10)),
              entry_price: parseFloat(p.avg_entry_price || "0"),
              current_price: parseFloat(p.current_price || "0"),
              cost_basis: parseFloat(p.cost_basis || "0"),
              market_value: parseFloat(p.market_value || "0"),
              unrealized_pnl: parseFloat(p.unrealized_pl || "0"),
              unrealized_pnl_pct: pnlPct,
              peak_pnl_pct: Math.max(0, pnlPct),
            };
          });

          setPipelineState((prev) => ({
            ...prev,
            active_positions: mappedPositions,
          }));
        }
      }
    } catch {
      // Ignore background sync error
    }
  };

  // Alpaca Connect Handler
  const handleConnectAlpaca = async (apiKey: string, secretKey: string): Promise<boolean> => {
    try {
      const res = await fetch("/api/alpaca/account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ apiKey, secretKey }),
      });

      if (!res.ok) return false;

      const data = await res.json();
      setConnectedKeys({ apiKey, secretKey });
      setAccount({
        account_id: data.account_id,
        status: data.status,
        equity: data.equity,
        cash: data.cash,
        buying_power: data.buying_power,
        day_pnl: data.day_pnl,
        day_pnl_pct: data.day_pnl_pct,
        is_connected: true,
      });

      // Update baseline state
      setPipelineState((prev) => ({
        ...prev,
        account_id: data.account_id,
        equity: data.equity,
        buying_power: data.buying_power,
        remaining_capital: data.cash,
      }));

      // Initial positions sync
      await syncAlpacaAccountAndPositions(apiKey, secretKey);
      return true;
    } catch {
      return false;
    }
  };

  const handleDisconnect = () => {
    setConnectedKeys(null);
    setAccount((prev) => ({ ...prev, is_connected: false }));
  };

  // Launch the Real Python Agent Subprocess
  const handleLaunchAgent = async () => {
    if (!connectedKeys) return;

    try {
      setIsRunning(true);
      const res = await fetch("/api/agent/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          apiKey: connectedKeys.apiKey,
          secretKey: connectedKeys.secretKey,
        }),
      });

      if (!res.ok) {
        setIsRunning(false);
        return;
      }

      // Start SSE stream
      startLogStream();
    } catch {
      setIsRunning(false);
    }
  };

  const pendingLinesRef = useRef<string[]>([]);
  const parseTimerRef = useRef<NodeJS.Timeout | null>(null);

  const handleToggleFollowLive = (follow: boolean) => {
    setFollowLive(follow);
    followLiveRef.current = follow;
    if (follow) {
      setSelectedStageIndex(pipelineState.stage_index);
    }
  };

  // Connect to SSE Log Stream
  const startLogStream = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    accumulatedLogsRef.current = "";
    pendingLinesRef.current = [];
    setLogs([]);

    const es = new EventSource("/api/agent/stream");
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        const rawLine = JSON.parse(event.data);
        if (typeof rawLine !== "string") return;

        // Clean out dirty headers or PowerShell prompts
        if (
          rawLine.includes("# COMPLETELY OUTDATED") ||
          rawLine.includes("PS D:\\") ||
          rawLine.includes("PS C:\\")
        ) {
          return;
        }

        pendingLinesRef.current.push(rawLine);
        accumulatedLogsRef.current += (accumulatedLogsRef.current ? "\n" : "") + rawLine;

        // Batch parsing and state updates to prevent freezing event loop or child process pipe
        if (!parseTimerRef.current) {
          parseTimerRef.current = setTimeout(() => {
            parseTimerRef.current = null;
            const chunk = pendingLinesRef.current;
            pendingLinesRef.current = [];
            if (chunk.length > 0) {
              setLogs((prev) => [...prev, ...chunk]);
            }

            setPipelineState((prev) => parseConsoleText(accumulatedLogsRef.current, prev));
          }, 80);
        }
      } catch {
        // Ignore parse error on single line
      }
    };

    es.onerror = () => {
      // EventSource reconnects automatically
    };
  };

  // Visual Pacer: smooth artificial visual delays so instant deterministic steps and research can be absorbed
  useEffect(() => {
    if (!followLive) return;

    const targetStage = pipelineState.stage_index;

    // If a new cycle started (target is behind current), reset immediately
    if (selectedStageIndex > targetStage) {
      setSelectedStageIndex(targetStage);
      return;
    }

    // If target is ahead, smoothly pace through each step
    if (selectedStageIndex < targetStage) {
      const stageDelays = [
        1500, // Step 0 Universe -> Step 1 Scanner
        1800, // Step 1 Scanner -> Step 2 Research
        3200, // Step 2 Research -> Step 3 Stock Rank (allows watching dossiers reveal)
        1800, // Step 3 Stock Rank -> Step 4 BSM Options
        1800, // Step 4 BSM Options -> Step 5 Global Rank
        1800, // Step 5 Global Rank -> Step 6 Portfolio Plan
        1500, // Step 6 Portfolio -> Step 7 Risk Gate
        1500, // Step 7 Risk Gate -> Step 8 Execution
        1800, // Step 8 Execution -> Step 9 Exit Loop
        0,
      ];
      const delay = stageDelays[selectedStageIndex] ?? 1500;

      const timer = setTimeout(() => {
        setSelectedStageIndex((prev) => (prev < targetStage ? prev + 1 : prev));
      }, delay);

      return () => clearTimeout(timer);
    }
  }, [selectedStageIndex, pipelineState.stage_index, followLive]);

  // Stop Agent Subprocess
  const handleStopAgent = async () => {
    try {
      await fetch("/api/agent/stop", { method: "POST" });
    } finally {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setIsRunning(false);
    }
  };

  // Reset State
  const handleReset = async () => {
    await handleStopAgent();
    setPipelineState(createInitialState());
    setLogs([]);
    setSelectedStageIndex(0);
    accumulatedLogsRef.current = "";
  };

  // Background position and account poller every 7 seconds
  useEffect(() => {
    if (!connectedKeys) return;

    const interval = setInterval(() => {
      syncAlpacaAccountAndPositions(connectedKeys.apiKey, connectedKeys.secretKey);
    }, 7000);

    return () => clearInterval(interval);
  }, [connectedKeys]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  // Render Dynamic Stage Workspace based on selectedStageIndex
  const renderStageWorkspace = () => {
    switch (selectedStageIndex) {
      case 0:
        return <UniverseView state={pipelineState} />;
      case 1:
        return <ScannerView state={pipelineState} />;
      case 2:
        return <ResearchView state={pipelineState} />;
      case 3:
        return <StockRankView state={pipelineState} />;
      case 4:
        return <OptionsChainView state={pipelineState} />;
      case 5:
        return <GlobalRankView state={pipelineState} />;
      case 6:
        return <PortfolioView state={pipelineState} />;
      case 7:
        return <RiskGateView state={pipelineState} />;
      case 8:
        return <ExecutionView state={pipelineState} />;
      case 9:
        return <ExitLoopView state={pipelineState} />;
      default:
        return <UniverseView state={pipelineState} />;
    }
  };

  return (
    <div className="app-layout">
      <Header
        theme={theme}
        onToggleTheme={toggleTheme}
        onReset={handleReset}
        isRunning={isRunning}
      />

      <main className="main-content">
        {/* Left Column: Connection & Compact Financial Overview */}
        <LeftRail
          account={account}
          onConnect={handleConnectAlpaca}
          onDisconnect={handleDisconnect}
          onLaunchAgent={handleLaunchAgent}
          onStopAgent={handleStopAgent}
          isRunning={isRunning}
          pipelineState={pipelineState}
        />

        {/* Right Canvas: 10-Stage Stepper, Active Stage Canvas, Real-Time Console Box */}
        <section style={{ display: "flex", flexDirection: "column", minWidth: 0, width: "100%", maxWidth: "100%" }}>
          <PipelineStepper
            currentStageIndex={pipelineState.stage_index}
            selectedStageIndex={selectedStageIndex}
            onSelectStage={setSelectedStageIndex}
            followLive={followLive}
            onToggleFollowLive={handleToggleFollowLive}
          />

          {renderStageWorkspace()}

          <ExecutionConsole logs={logs} isRunning={isRunning} />
        </section>
      </main>
    </div>
  );
}
