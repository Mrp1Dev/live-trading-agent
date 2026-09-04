import { NextRequest, NextResponse } from "next/server";
import { serverAgentRunner } from "@/lib/serverRunner";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const apiKey = body.apiKey || process.env.ALPACA_API_KEY || "";
    const secretKey = body.secretKey || process.env.ALPACA_SECRET_KEY || "";

    if (!apiKey || !secretKey) {
      return NextResponse.json(
        { error: "Please provide both Alpaca API Key and Secret Key" },
        { status: 400 }
      );
    }

    const started = serverAgentRunner.start(apiKey, secretKey, true);
    if (!started) {
      const status = serverAgentRunner.getStatus();
      if (status.isRunning) {
        return NextResponse.json({ message: "Agent is already running", status });
      }
      return NextResponse.json({ error: "Failed to spawn python agent process" }, { status: 500 });
    }

    return NextResponse.json({
      success: true,
      message: "Agent spawned successfully in isolated mode",
      status: serverAgentRunner.getStatus(),
    });
  } catch (err: any) {
    return NextResponse.json({ error: err.message || "Internal server error" }, { status: 500 });
  }
}
