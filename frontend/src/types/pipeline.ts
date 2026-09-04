export interface ScannedStock {
  rank: number;
  symbol: string;
  price: number;
  ret_1d: string;
  ret_5d: string;
  ret_20d: string;
  vol_ratio: string;
  sma20_dist: string;
  realized_vol: string;
  rs_vs_spy: string;
  score: number;
}

export interface ResearchReport {
  symbol: string;
  articles_count: number;
  quant_summary: string;
  news_summary: string;
  catalyst: string;
  risk: string;
  timing: string;
  sources: string[];
}

export interface LLMStockRank {
  rank: number;
  symbol: string;
  scanner_rank: number | string;
  scanner_score: number;
  status?: string;
}

export interface OptionCandidate {
  id: string;
  symbol: string;
  stock: string;
  option_type: "call" | "put" | string;
  expiration: string;
  dte: number;
  strike: number;
  moneyness: string;
  ask: number;
  spread_pct: string;
  iv: string;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  selector_score: number;
}

export interface TopRankedOption {
  rank: number;
  symbol: string;
  option_type: string;
  strike: number;
  dte: number;
  mid: number;
  spread_pct: string;
  delta: number;
  score: number;
}

export interface PlannedTrade {
  rank: number;
  ticker: string;
  direction: string;
  contracts: number;
  premium: number;
  max_loss: number;
  trade_score: number;
  option_symbol: string;
}

export interface RiskAssessment {
  total_max_loss: number;
  max_loss_pct: number;
  bullish_risk_pct: number;
  bearish_risk_pct: number;
  portfolio_delta: number;
  portfolio_gamma: number;
  portfolio_vega: number;
  portfolio_theta: number;
  approved_positions: Array<{
    symbol: string;
    direction: string;
    contracts: number;
    risk: number;
    score: number;
    option_symbol: string;
  }>;
  rejected_positions: string[];
}

export interface ExecutionItem {
  symbol: string;
  action: string;
  requested_qty: number;
  submitted_qty: number;
  filled_qty: number;
  status: string;
  order_id: string;
  limit_price: number;
  result: string;
}

export interface LivePosition {
  symbol: string;
  underlying: string;
  direction: string;
  qty: number;
  entry_price: number;
  current_price: number;
  cost_basis: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  peak_pnl_pct: number;
  dte?: number;
  trade_score?: number;
}

export interface PipelineState {
  stage_index: number;
  stage_name: string;
  cycle_count: number;
  last_update: string;
  account_id: string;
  equity: number;
  buying_power: number;
  universe_size: number;
  scanner_picks: ScannedStock[];
  research_reports: Record<string, ResearchReport>;
  llm_rankings: LLMStockRank[];
  skipped_symbols: Record<string, string>;
  option_candidates: OptionCandidate[];
  top_options: TopRankedOption[];
  planned_trades: PlannedTrade[];
  total_premium_deployed: number;
  total_planned_max_loss: number;
  remaining_capital: number;
  risk_assessment: RiskAssessment | null;
  execution_items: ExecutionItem[];
  active_positions: LivePosition[];
}

export interface MarketClockState {
  timestamp_et: string;
  is_open: boolean;
  next_open_et?: string;
  next_close_et?: string;
}

export interface AccountState {
  account_id: string;
  status: string;
  equity: number;
  cash: number;
  buying_power: number;
  day_pnl: number;
  day_pnl_pct: number;
  is_connected: boolean;
}
