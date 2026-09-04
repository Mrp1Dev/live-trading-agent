import { ChildProcess, spawn } from "child_process";
import fs from "fs";
import path from "path";

export interface AgentStatus {
  isRunning: boolean;
  pid: number | null;
  uptimeSeconds: number;
  exitCode: number | null;
}

type LogListener = (line: string) => void;

class ServerAgentRunner {
  private static instance: ServerAgentRunner | null = null;

  public process: ChildProcess | null = null;
  public buffer: string[] = [];
  private listeners: Set<LogListener> = new Set();
  private startTime: Date | null = null;
  private exitCode: number | null = null;

  public static getInstance(): ServerAgentRunner {
    if (!(global as any)._serverAgentRunnerInstance) {
      (global as any)._serverAgentRunnerInstance = new ServerAgentRunner();
    }
    return (global as any)._serverAgentRunnerInstance;
  }

  public getStatus(): AgentStatus {
    const isRunning = this.process !== null && this.process.exitCode === null;
    const uptimeSeconds = isRunning && this.startTime
      ? Math.floor((Date.now() - this.startTime.getTime()) / 1000)
      : 0;

    return {
      isRunning,
      pid: this.process?.pid || null,
      uptimeSeconds,
      exitCode: this.exitCode,
    };
  }

  public getBuffer(): string[] {
    return [...this.buffer];
  }

  public addListener(listener: LogListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private broadcastLine(line: string): void {
    // Filter out unwanted shell prompt or comment lines
    if (line.includes("# COMPLETELY OUTDATED") || line.includes("PS D:\\") || line.includes("PS C:\\")) {
      return;
    }
    this.buffer.push(line);
    if (this.buffer.length > 5000) {
      this.buffer = this.buffer.slice(-4000);
    }
    for (const listener of this.listeners) {
      try {
        listener(line);
      } catch {
        // Ignore dead listener
      }
    }
  }

  public start(apiKey: string, secretKey: string, confirmPaper: boolean = true): boolean {
    if (this.process && this.process.exitCode === null) {
      return false; // Already running
    }

    this.buffer = [];
    this.startTime = new Date();
    this.exitCode = null;

    const initialCmd = `> python main.py ${confirmPaper ? "--confirm-paper-trades" : ""}`.trim();
    this.broadcastLine(initialCmd);

    // Resolve project root (parent directory of frontend/)
    const projectRoot = path.resolve(process.cwd(), "..");
    const venvPythonWin = path.join(projectRoot, ".venv", "Scripts", "python.exe");
    const venvPythonPosix = path.join(projectRoot, ".venv", "bin", "python");

    let pythonExe = "python";
    if (process.platform === "win32" && fs.existsSync(venvPythonWin)) {
      pythonExe = venvPythonWin;
    } else if (fs.existsSync(venvPythonPosix)) {
      pythonExe = venvPythonPosix;
    }

    const args = ["-u", "main.py"];
    if (confirmPaper) {
      args.push("--confirm-paper-trades");
    }

    // Read projectRoot .env to guarantee Featherless API keys are passed to subprocess
    const envPath = path.join(projectRoot, ".env");
    const envFileVars: Record<string, string> = {};
    if (fs.existsSync(envPath)) {
      try {
        const content = fs.readFileSync(envPath, "utf-8");
        for (const rawLine of content.split(/\r?\n/)) {
          const line = rawLine.trim();
          if (!line || line.startsWith("#")) continue;
          const eq = line.indexOf("=");
          if (eq !== -1) {
            const key = line.slice(0, eq).trim();
            const val = line.slice(eq + 1).trim();
            envFileVars[key] = val;
          }
        }
      } catch {
        // Fallback to process.env
      }
    }

    // Strict process isolation & guaranteed key inheritance
    const env: NodeJS.ProcessEnv = {
      ...process.env,
      ...envFileVars,
      PYTHONUNBUFFERED: "1",
      ALPACA_API_KEY: apiKey.trim(),
      ALPACA_SECRET_KEY: secretKey.trim(),
      APCA_API_KEY_ID: apiKey.trim(),
      APCA_API_SECRET_KEY: secretKey.trim(),
      ALPACA_STATE_FILE: "state/dashboard_positions.json",
      ALPACA_JOURNAL_PATH: "logs/dashboard_execution.jsonl",
      EXECUTION_LIMIT_BUFFER_PCT: "0.02",
      EXECUTION_MAX_PRICE_MOVE_PCT: "0.15",
      EXECUTION_MAX_INTENT_AGE_SEC: "300",
    };

    try {
      this.process = spawn(pythonExe, args, {
        cwd: projectRoot,
        env,
        stdio: ["ignore", "pipe", "pipe"],
      });

      let stdoutBuffer = "";
      this.process.stdout?.on("data", (chunk: Buffer) => {
        stdoutBuffer += chunk.toString("utf-8");
        const lines = stdoutBuffer.split(/\r?\n/);
        stdoutBuffer = lines.pop() || "";
        for (const line of lines) {
          this.broadcastLine(line);
        }
      });

      let stderrBuffer = "";
      this.process.stderr?.on("data", (chunk: Buffer) => {
        stderrBuffer += chunk.toString("utf-8");
        const lines = stderrBuffer.split(/\r?\n/);
        stderrBuffer = lines.pop() || "";
        for (const line of lines) {
          this.broadcastLine(`[STDERR] ${line}`);
        }
      });

      this.process.on("close", (code) => {
        this.exitCode = code;
        this.broadcastLine(`\n[PROCESS TERMINATED] Exit code: ${code}`);
        this.process = null;
      });

      this.process.on("error", (err) => {
        this.broadcastLine(`[PROCESS ERROR] Failed to start python: ${err.message}`);
        this.process = null;
      });

      return true;
    } catch (err: any) {
      this.broadcastLine(`[SPAWN ERROR] ${err.message}`);
      return false;
    }
  }

  public stop(): boolean {
    if (!this.process || this.process.exitCode !== null) {
      return false;
    }

    try {
      const pid = this.process.pid;
      this.broadcastLine("\n^C");
      this.broadcastLine("Interrupted. Open positions are LEFT OPEN and unmanaged - re-run to resume management, or flatten from the dashboard.");
      this.broadcastLine("[PROCESS STOPPED] Ready to resume with python main.py --confirm-paper-trades\n");
      if (pid) {
        if (process.platform === "win32") {
          spawn("taskkill", ["/F", "/T", "/PID", pid.toString()]);
        } else {
          this.process.kill("SIGINT");
        }
      }
      this.process = null;
      return true;
    } catch {
      return false;
    }
  }
}

export const serverAgentRunner = ServerAgentRunner.getInstance();
