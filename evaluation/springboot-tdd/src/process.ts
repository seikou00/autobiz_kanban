import { spawn } from "node:child_process"
import { performance } from "node:perf_hooks"

import type { ProcessResult } from "./types.ts"

export interface RunProcessOptions {
  cwd: string
  env?: NodeJS.ProcessEnv
  timeoutMs?: number
  maxOutputChars?: number
}

export async function runProcess(argv: string[], options: RunProcessOptions): Promise<ProcessResult> {
  if (argv.length === 0) throw new Error("argv must not be empty")
  const started = performance.now()
  const maxOutputChars = options.maxOutputChars ?? 2_000_000
  return await new Promise((resolveResult, reject) => {
    const child = spawn(argv[0]!, argv.slice(1), {
      cwd: options.cwd,
      env: options.env ?? process.env,
      stdio: ["ignore", "pipe", "pipe"]
    })
    let stdout = ""
    let stderr = ""
    let timedOut = false
    let forceKill: NodeJS.Timeout | null = null
    const append = (current: string, chunk: Buffer): string => {
      const value = current + chunk.toString("utf8")
      return value.length > maxOutputChars ? value.slice(value.length - maxOutputChars) : value
    }
    child.stdout.on("data", (chunk: Buffer) => {
      stdout = append(stdout, chunk)
    })
    child.stderr.on("data", (chunk: Buffer) => {
      stderr = append(stderr, chunk)
    })
    child.on("error", reject)
    const timeout = options.timeoutMs
      ? setTimeout(() => {
          timedOut = true
          child.kill("SIGTERM")
          forceKill = setTimeout(() => child.kill("SIGKILL"), 5_000)
        }, options.timeoutMs)
      : null
    child.on("close", (exitCode, signal) => {
      if (timeout) clearTimeout(timeout)
      if (forceKill) clearTimeout(forceKill)
      resolveResult({
        argv: [...argv],
        cwd: options.cwd,
        exitCode,
        signal,
        stdout,
        stderr,
        durationMs: Math.round(performance.now() - started),
        timedOut
      })
    })
  })
}
