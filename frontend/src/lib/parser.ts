import {
  ExecutionItem,
  LivePosition,
  LLMStockRank,
  OptionCandidate,
  PipelineState,
  PlannedTrade,
  ResearchReport,
  RiskAssessment,
  ScannedStock,
  TopRankedOption,
} from "../types/pipeline";

export const STAGE_NAMES = [
  "1. Universe",
  "2. Scanner",
  "3. Research",
  "4. Stock Rank",
  "5. BSM Options",
  "6. Global Rank",
  "7. Portfolio",
  "8. Risk Gate",
  "9. Execution",
  "10. Exit Loop",
];

// 318 symbols as confirmed from strategy/universe.py
export const DEFAULT_UNIVERSE_SIZE = 318;

export function createInitialState(): PipelineState {
  return {
    stage_index: 0,
    stage_name: STAGE_NAMES[0],
    cycle_count: 1,
    last_update: new Date().toLocaleTimeString("en-US", { timeZone: "America/New_York" }) + " ET",
    account_id: "",
    equity: 100000.0,
    buying_power: 400000.0,
    universe_size: DEFAULT_UNIVERSE_SIZE,
    scanner_picks: [],
    research_reports: {},
    llm_rankings: [],
    skipped_symbols: {},
    option_candidates: [],
    top_options: [],
    planned_trades: [],
    total_premium_deployed: 0,
    total_planned_max_loss: 0,
    remaining_capital: 100000.0,
    risk_assessment: null,
    execution_items: [],
    active_positions: [],
  };
}

export function parseConsoleText(rawText: string, existingState?: PipelineState): PipelineState {
  // 1. Sanitize text by removing outdated file headers or shell prompt noise
  const sanitizedText = rawText
    .split(/\r?\n/)
    .filter(
      (line) =>
        !line.includes("# COMPLETELY OUTDATED") &&
        !line.includes("PS D:\\") &&
        !line.includes("PS C:\\")
    )
    .join("\n");

  const state: PipelineState = existingState ? { ...existingState } : createInitialState();

  // 2. Detect multiple cycles: count occurrences of "Scanning ... stock universe"
  const cycleMatches = sanitizedText.match(/Scanning\s+(\d+)-stock universe/gi);
  if (cycleMatches && cycleMatches.length > 0) {
    state.cycle_count = cycleMatches.length;
    const lastCycleMatch = /Scanning\s+(\d+)-stock universe/i.exec(cycleMatches[cycleMatches.length - 1]);
    if (lastCycleMatch) {
      state.universe_size = parseInt(lastCycleMatch[1], 10);
    }
  }

  // Work with the current active cycle portion if multiple cycles exist
  const lastCycleIndex = sanitizedText.lastIndexOf("Scanning ");
  const currentCycleText = lastCycleIndex !== -1 ? sanitizedText.slice(lastCycleIndex) : sanitizedText;

  // 3. Account details
  const accMatch = sanitizedText.match(/Account ID:\s*([a-f0-9-]+)/i);
  if (accMatch) state.account_id = accMatch[1].trim();

  const eqMatch = sanitizedText.match(/Equity:\s*\$([0-9,]+(\.[0-9]{2})?)/i);
  if (eqMatch) state.equity = parseFloat(eqMatch[1].replace(/,/g, ""));

  const bpMatch = sanitizedText.match(/Buying power:\s*\$([0-9,]+(\.[0-9]{2})?)/i);
  if (bpMatch) state.buying_power = parseFloat(bpMatch[1].replace(/,/g, ""));

  // 4. Stage index detection based on the latest cycle
  if (
    currentCycleText.includes("FILL VERIFICATION") ||
    currentCycleText.includes("order(s) filled") ||
    currentCycleText.includes("ACTIVE POSITIONS") ||
    currentCycleText.includes("Open positions:") ||
    currentCycleText.includes("Trailing stop") ||
    currentCycleText.includes("Session over") ||
    currentCycleText.includes("Cadence: exits every")
  ) {
    state.stage_index = 9; // Step 10: Exit Loop / Position Monitor
  } else if (currentCycleText.includes("EXECUTION REPORT") || currentCycleText.includes("PAPER TRADE EXECUTION CONFIRMATION")) {
    state.stage_index = 8;
  } else if (currentCycleText.includes("RISK ASSESSMENT") || currentCycleText.includes("APPROVED POSITIONS")) {
    state.stage_index = 7;
  } else if (currentCycleText.includes("PORTFOLIO PLAN")) {
    state.stage_index = 6;
  } else if (
    currentCycleText.includes("TOP RANKED OPTIONS (GLOBAL)") ||
    currentCycleText.includes("RAW OPTION GLOBAL LLM RESPONSE") ||
    currentCycleText.includes("OPTION LLM GLOBAL RANKER INPUT")
  ) {
    state.stage_index = 5;
  } else if (currentCycleText.includes("AVAILABLE OPTION POOL")) {
    state.stage_index = 4;
  } else if (
    currentCycleText.includes("LLM STOCK RANKING") ||
    currentCycleText.includes("LLM-RANKED STOCKS") ||
    currentCycleText.includes("RAW LLM RANKER RESPONSE") ||
    currentCycleText.includes("LLM RANKER INPUT") ||
    currentCycleText.includes("deterministic filtering") ||
    currentCycleText.includes("Skipping ")
  ) {
    state.stage_index = 3;
  } else if (currentCycleText.includes("RESEARCH REPORTS")) {
    state.stage_index = 2;
  } else if (currentCycleText.includes("TOP STOCK SCANNER PICKS")) {
    state.stage_index = 1;
  } else if (currentCycleText.includes("Scanning ") || currentCycleText.includes("Connected to Alpaca")) {
    state.stage_index = 0;
  }

  state.stage_name = STAGE_NAMES[state.stage_index];

  // 5. Parse Top Stock Scanner Picks
  // Format: #1 NOW $ 144.69 +4.52% +12.59% +30.05% 1.63x +15.3% 55.0% +27.04% 98.0
  const scannerRegex = /#(\d+)\s+([A-Z]+)\s+\$\s*([\d\.]+)\s+([+-]?[\d\.]+%)\s+([+-]?[\d\.]+%)\s+([+-]?[\d\.]+%)\s+([\d\.]+x)\s+([+-]?[\d\.]+%)\s+([\d\.]+%)\s+([+-]?[\d\.]+%)\s+([\d\.]+)/g;
  const picks: ScannedStock[] = [];
  let m: RegExpExecArray | null;
  while ((m = scannerRegex.exec(currentCycleText)) !== null) {
    picks.push({
      rank: parseInt(m[1], 10),
      symbol: m[2],
      price: parseFloat(m[3]),
      ret_1d: m[4],
      ret_5d: m[5],
      ret_20d: m[6],
      vol_ratio: m[7],
      sma20_dist: m[8],
      realized_vol: m[9],
      rs_vs_spy: m[10],
      score: parseFloat(m[11]),
    });
  }
  if (picks.length > 0) {
    state.scanner_picks = picks;
  }

  // 6. Parse Research Reports
  const reportRegex = /\[([A-Z]+)\]\s*\nArticles:\s*(\d+)\s*\nQuant:\s*([\s\S]*?)\s*\nNews:\s*([\s\S]*?)\s*\n(?:Catalyst:\s*([\s\S]*?)\s*\n)?(?:Risk:\s*([\s\S]*?)\s*\n)?Timing:\s*([\s\S]*?)(?=\n(?:\[[A-Z]+\]|={5,}|$))/g;
  while ((m = reportRegex.exec(currentCycleText)) !== null) {
    const symbol = m[1];
    const sources: string[] = [];
    const sourceRegex = /-\s*([^\n]+)/g;
    let sMatch: RegExpExecArray | null;
    const block = m[0];
    while ((sMatch = sourceRegex.exec(block)) !== null) {
      sources.push(sMatch[1].trim());
    }

    state.research_reports[symbol] = {
      symbol,
      articles_count: parseInt(m[2], 10),
      quant_summary: m[3]?.trim() || "",
      news_summary: m[4]?.trim() || "",
      catalyst: m[5]?.trim() || "None",
      risk: m[6]?.trim() || "None",
      timing: m[7]?.trim() || "UNKNOWN",
      sources,
    };
  }

  // 7. Parse LLM Stock Rankings (de-duplicate symbols so secondary LLM-RANKED STOCKS block doesn't repeat 1..10)
  const rankRegex = /#(\d+)\s+([A-Z]+)\s+Scanner=#(\d+)\s+ScannerScore=\s*([\d\.]+)/g;
  const rankings: LLMStockRank[] = [];
  const seenSymbols = new Set<string>();
  while ((m = rankRegex.exec(currentCycleText)) !== null) {
    const symbol = m[2];
    if (seenSymbols.has(symbol)) continue;
    seenSymbols.add(symbol);
    rankings.push({
      rank: parseInt(m[1], 10),
      symbol,
      scanner_rank: parseInt(m[3], 10),
      scanner_score: parseFloat(m[4]),
    });
  }
  if (rankings.length > 0) {
    state.llm_rankings = rankings;
  }

  // Parse skipped symbols
  const skipRegex = /Skipping\s+([A-Z]+):\s*([^\n]+)/g;
  while ((m = skipRegex.exec(currentCycleText)) !== null) {
    state.skipped_symbols[m[1]] = m[2].trim();
  }

  // 8. Parse Available Option Pool (Stage 5 BSM selector)
  const poolRegex = /(OPT\d+)\s*\|\s*symbol=([A-Z0-9]+)\s*\|\s*stock=([A-Z]+)\s*\|\s*(call|put)\s*\|\s*exp=([\d-]+)\s*\|\s*DTE=(\d+)\s*\|\s*strike=\$?([\d\.]+)\s*\|\s*mny=([+-]?[\d\.]+%)\s*\|\s*ask=\$?([\d\.]+)\s*\|\s*spread=([\d\.]+%)\s*\|\s*IV=([\d\.]+%)\s*\|\s*delta=([+-]?[\d\.]+)\s*\|\s*gamma=([+-]?[\d\.]+)\s*\|\s*theta=([+-]?[\d\.]+)\s*\|\s*vega=([+-]?[\d\.]+)\s*\|\s*selector=([\d\.]+)/g;
  const candidates: OptionCandidate[] = [];
  while ((m = poolRegex.exec(currentCycleText)) !== null) {
    candidates.push({
      id: m[1],
      symbol: m[2],
      stock: m[3],
      option_type: m[4],
      expiration: m[5],
      dte: parseInt(m[6], 10),
      strike: parseFloat(m[7]),
      moneyness: m[8],
      ask: parseFloat(m[9]),
      spread_pct: m[10],
      iv: m[11],
      delta: parseFloat(m[12]),
      gamma: parseFloat(m[13]),
      theta: parseFloat(m[14]),
      vega: parseFloat(m[15]),
      selector_score: parseFloat(m[16]),
    });
  }
  if (candidates.length > 0) {
    state.option_candidates = candidates;
  }

  // 9. Parse Top Ranked Options (Global Stage 6)
  const topOptRegex = /#(\d+)\s+([A-Z0-9]+)\s+Type=([A-Z]+)\s+Strike=\$?([\d\.]+)\s+DTE=(\d+)\s+Mid=\$?([\d\.]+)\s+Spread=([\d\.]+%)\s+Delta=([+-]?[\d\.]+)\s+Score=\s*([\d\.]+)/g;
  const topOpts: TopRankedOption[] = [];
  while ((m = topOptRegex.exec(currentCycleText)) !== null) {
    topOpts.push({
      rank: parseInt(m[1], 10),
      symbol: m[2],
      option_type: m[3],
      strike: parseFloat(m[4]),
      dte: parseInt(m[5], 10),
      mid: parseFloat(m[6]),
      spread_pct: m[7],
      delta: parseFloat(m[8]),
      score: parseFloat(m[9]),
    });
  }
  if (topOpts.length > 0) {
    state.top_options = topOpts;
  }

  // 10. Parse Portfolio Plan (Stage 7)
  // Format in console:
  // #1    CRM     BULLISH            3$    2,319.00$    2,319.00       100.0  CRM260904C00255000
  // or: #1    NVDA    BULLISH            7$    2,170.00$    2,170.00        #1  NVDA260909C00230000
  const planRegex = /#?(\d+)\s+([A-Z]+)\s+([A-Z]+)\s+(\d+)\s*\$?\s*([0-9,]+\.[0-9]{2})\s*\$?\s*([0-9,]+\.[0-9]{2})\s+#?([0-9\.]+)\s+([A-Z0-9]+)/g;
  const trades: PlannedTrade[] = [];
  while ((m = planRegex.exec(currentCycleText)) !== null) {
    trades.push({
      rank: parseInt(m[1], 10),
      ticker: m[2],
      direction: m[3],
      contracts: parseInt(m[4], 10),
      premium: parseFloat(m[5].replace(/,/g, "")),
      max_loss: parseFloat(m[6].replace(/,/g, "")),
      trade_score: parseFloat(m[7]),
      option_symbol: m[8],
    });
  }
  if (trades.length > 0) {
    state.planned_trades = trades;
  }

  const premMatch = currentCycleText.match(/Total premium deployed:\s*\$([0-9,]+(\.[0-9]{2})?)/i);
  if (premMatch) state.total_premium_deployed = parseFloat(premMatch[1].replace(/,/g, ""));

  const lossMatch = currentCycleText.match(/Total planned max loss:\s*\$([0-9,]+(\.[0-9]{2})?)/i);
  if (lossMatch) state.total_planned_max_loss = parseFloat(lossMatch[1].replace(/,/g, ""));

  const remMatch = currentCycleText.match(/Remaining capital:\s*\$([0-9,]+(\.[0-9]{2})?)/i);
  if (remMatch) state.remaining_capital = parseFloat(remMatch[1].replace(/,/g, ""));

  // Fallback: If planned_trades was not populated from table text but approved_positions exist
  if (state.planned_trades.length === 0 && state.risk_assessment && state.risk_assessment.approved_positions.length > 0) {
    state.planned_trades = state.risk_assessment.approved_positions.map((p, idx) => ({
      rank: idx + 1,
      ticker: p.symbol,
      direction: p.direction,
      contracts: p.contracts,
      premium: p.risk,
      max_loss: p.risk,
      trade_score: p.score,
      option_symbol: p.option_symbol,
    }));
  }

  if (state.total_premium_deployed === 0 && state.planned_trades.length > 0) {
    state.total_premium_deployed = state.planned_trades.reduce((acc, t) => acc + t.premium, 0);
  }
  if (state.total_planned_max_loss === 0 && state.planned_trades.length > 0) {
    state.total_planned_max_loss = state.planned_trades.reduce((acc, t) => acc + t.max_loss, 0);
  }
  if (state.remaining_capital === 0 && state.equity > 0 && state.total_premium_deployed > 0) {
    state.remaining_capital = Math.max(0, state.equity - state.total_premium_deployed);
  }

  // 11. Parse Risk Assessment (Stage 8)
  const riskMaxLossMatch = currentCycleText.match(/Total max loss:\s*\$([0-9,]+(\.[0-9]{2})?)\s*\(([\d\.]+)%\)/i);
  const bullRiskMatch = currentCycleText.match(/Bullish risk:\s*([\d\.]+)%/i);
  const bearRiskMatch = currentCycleText.match(/Bearish risk:\s*([\d\.]+)%/i);
  const deltaMatch = currentCycleText.match(/Portfolio Delta:\s*([+-]?[\d\.]+)/i);
  const gammaMatch = currentCycleText.match(/Portfolio Gamma:\s*([+-]?[\d\.]+)/i);
  const vegaMatch = currentCycleText.match(/Portfolio Vega:\s*([+-]?[\d\.]+)/i);
  const thetaMatch = currentCycleText.match(/Portfolio Theta:\s*([+-]?[\d\.]+)/i);

  if (riskMaxLossMatch) {
    const approvedRegex = /([A-Z]+)\s+(BULLISH|BEARISH)\s+(\d+)\s+contracts\s+risk=\$\s*([0-9,]+\.[0-9]{2})\s+score=\s*([\d\.]+)\s+([A-Z0-9]+)/g;
    const approved: RiskAssessment["approved_positions"] = [];
    while ((m = approvedRegex.exec(currentCycleText)) !== null) {
      approved.push({
        symbol: m[1],
        direction: m[2],
        contracts: parseInt(m[3], 10),
        risk: parseFloat(m[4].replace(/,/g, "")),
        score: parseFloat(m[5]),
        option_symbol: m[6],
      });
    }

    state.risk_assessment = {
      total_max_loss: parseFloat(riskMaxLossMatch[1].replace(/,/g, "")),
      max_loss_pct: parseFloat(riskMaxLossMatch[3]),
      bullish_risk_pct: bullRiskMatch ? parseFloat(bullRiskMatch[1]) : 0,
      bearish_risk_pct: bearRiskMatch ? parseFloat(bearRiskMatch[1]) : 0,
      portfolio_delta: deltaMatch ? parseFloat(deltaMatch[1]) : 0,
      portfolio_gamma: gammaMatch ? parseFloat(gammaMatch[1]) : 0,
      portfolio_vega: vegaMatch ? parseFloat(vegaMatch[1]) : 0,
      portfolio_theta: thetaMatch ? parseFloat(thetaMatch[1]) : 0,
      approved_positions: approved,
      rejected_positions: [],
    };
  }

  // 12. Parse Execution Report (Stage 9)
  const execRegex = /(\d+)\.\s+([A-Z0-9]+)\s*\n\s*Action:\s*([^\n]+)\s*\n\s*Requested:\s*(\d+)\s*\n\s*Submitted:\s*(\d+)\s*\n\s*Filled:\s*(\d+)\s*\n\s*Status:\s*([^\n]+)\s*\n\s*Order ID:\s*([a-f0-9-]+)\s*\n\s*Limit price:\s*\$?([\d\.]+)\s*\n\s*Result:\s*([^\n]+)/g;
  const execs: ExecutionItem[] = [];
  while ((m = execRegex.exec(currentCycleText)) !== null) {
    execs.push({
      symbol: m[2],
      action: m[3]?.trim() || "BUY TO OPEN",
      requested_qty: parseInt(m[4], 10),
      submitted_qty: parseInt(m[5], 10),
      filled_qty: parseInt(m[6], 10),
      status: m[7]?.trim() || "SUBMITTED",
      order_id: m[8]?.trim() || "",
      limit_price: parseFloat(m[9]),
      result: m[10]?.trim() || "Order submitted to Alpaca paper account.",
    });
  }
  if (execs.length > 0) {
    state.execution_items = execs;
  }

  // 13. Parse Fill Verification
  // Format: NVDA260909C00230000: filled 7 @ $3.10
  const fillRegex = /([A-Z0-9]+):\s*filled\s+(\d+)\s*@\s*\$([\d\.]+)/g;
  const verifiedFills: { symbol: string; qty: number; price: number }[] = [];
  while ((m = fillRegex.exec(currentCycleText)) !== null) {
    verifiedFills.push({
      symbol: m[1],
      qty: parseInt(m[2], 10),
      price: parseFloat(m[3]),
    });
  }

  if (verifiedFills.length > 0) {
    for (const vf of verifiedFills) {
      const item = state.execution_items.find((ex) => ex.symbol === vf.symbol);
      if (item) {
        item.filled_qty = vf.qty;
        item.status = "FILLED";
      }
    }

    if (state.active_positions.length === 0) {
      state.active_positions = verifiedFills.map((vf) => {
        const tickerMatch = vf.symbol.match(/^([A-Z]+)\d{6}[CP]\d+/);
        const underlying = tickerMatch ? tickerMatch[1] : vf.symbol;
        return {
          symbol: vf.symbol,
          underlying,
          direction: "BULLISH",
          qty: vf.qty,
          entry_price: vf.price,
          current_price: vf.price,
          cost_basis: vf.qty * vf.price * 100,
          market_value: vf.qty * vf.price * 100,
          unrealized_pnl: 0.0,
          unrealized_pnl_pct: 0.0,
          peak_pnl_pct: 0.0,
          dte: 7,
          decision: "HOLD",
        };
      });
    }
  }

  return state;
}
