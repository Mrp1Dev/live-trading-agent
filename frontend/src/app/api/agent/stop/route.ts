import { NextResponse } from "next/server";
import { serverAgentRunner } from "@/lib/serverRunner";

export const runtime = "nodejs";

export async function POST() {
  try {
    const stopped = serverAgentRunner.stop();
    return NextResponse.json({
      success: stopped,
      message: stopped ? "Agent stopped safely" : "Agent was not running",
      status: serverAgentRunner.getStatus(),
    });
  } catch (err: any) {
    return NextResponse.json({ error: err.message || "Internal server error" }, { status: 500 });
  }
}
