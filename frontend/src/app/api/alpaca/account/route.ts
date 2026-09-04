import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const apiKey = body.apiKey || process.env.ALPACA_API_KEY;
    const secretKey = body.secretKey || process.env.ALPACA_SECRET_KEY;

    if (!apiKey || !secretKey) {
      return NextResponse.json({ error: "Missing API Key or Secret Key" }, { status: 400 });
    }

    const res = await fetch("https://paper-api.alpaca.markets/v2/account", {
      headers: {
        "APCA-API-KEY-ID": apiKey.trim(),
        "APCA-API-SECRET-KEY": secretKey.trim(),
      },
      cache: "no-store",
    });

    if (!res.ok) {
      const errText = await res.text();
      return NextResponse.json({ error: `Alpaca API error: ${res.statusText}`, details: errText }, { status: res.status });
    }

    const data = await res.json();
    const equity = parseFloat(data.equity || "0");
    const lastEquity = parseFloat(data.last_equity || data.equity || "0");
    const dayPnl = equity - lastEquity;
    const dayPnlPct = lastEquity > 0 ? (dayPnl / lastEquity) * 100 : 0.0;

    return NextResponse.json({
      account_id: data.id,
      status: data.status,
      equity,
      cash: parseFloat(data.cash || "0"),
      buying_power: parseFloat(data.buying_power || "0"),
      day_pnl: dayPnl,
      day_pnl_pct: dayPnlPct,
      currency: data.currency,
    });
  } catch (error: any) {
    return NextResponse.json({ error: error.message || "Internal server error" }, { status: 500 });
  }
}
