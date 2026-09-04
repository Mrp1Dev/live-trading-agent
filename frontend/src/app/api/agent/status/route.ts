import { NextResponse } from "next/server";
import { serverAgentRunner } from "@/lib/serverRunner";

export const runtime = "nodejs";

export async function GET() {
  return NextResponse.json({
    status: serverAgentRunner.getStatus(),
    bufferCount: serverAgentRunner.getBuffer().length,
  });
}
