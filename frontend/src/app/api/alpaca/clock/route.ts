import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const apiKey = body.apiKey || process.env.ALPACA_API_KEY;
    const secretKey = body.secretKey || process.env.ALPACA_SECRET_KEY;

    if (!apiKey || !secretKey) {
      return NextResponse.json({ error: "Missing API Key or Secret Key" }, { status: 400 });
    }

    const res = await fetch("https://paper-api.alpaca.markets/v2/clock", {
      headers: {
        "APCA-API-KEY-ID": apiKey.trim(),
        "APCA-API-SECRET-KEY": secretKey.trim(),
      },
      cache: "no-store",
    });

    if (!res.ok) {
      return NextResponse.json({ error: `Alpaca API error: ${res.statusText}` }, { status: res.status });
    }

    const clock = await res.json();
    return NextResponse.json(clock);
  } catch (error: any) {
    return NextResponse.json({ error: error.message || "Internal server error" }, { status: 500 });
  }
}
