#!/usr/bin/env node

import readline from 'node:readline'

const args = process.argv.slice(2)
if (args.some((arg) => arg !== '--raw')) {
  process.stderr.write('Usage: render-agent-events.mjs [--raw]\n')
  process.exit(2)
}

const rawOutput = args.includes('--raw')
const startedAt = Date.now()
const heartbeatSeconds = parseHeartbeatSeconds(process.env.AGENT_EVENT_HEARTBEAT_SECONDS)
const activeTools = new Map()

let nextToolSequence = 1
let agentStarted = false
let agentEnded = false
let agentFailed = false
let agentFailureReported = false
let rendererFailed = false
let assistantLineOpen = false
let lastOutputAt = startedAt
let lastHeartbeatAt = 0

function parseHeartbeatSeconds(value) {
  if (value === undefined || value === '') return 30
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return 30
  return parsed
}

function elapsedLabel(now = Date.now()) {
  const elapsedSeconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  const hours = Math.floor(elapsedSeconds / 3600)
  const minutes = Math.floor((elapsedSeconds % 3600) / 60)
  const seconds = elapsedSeconds % 60

  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }

  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

function durationLabel(started, ended = Date.now()) {
  const durationMs = Math.max(0, ended - started)
  if (durationMs < 1000) return `${durationMs} ms`
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)}s`

  const minutes = Math.floor(durationMs / 60_000)
  const seconds = Math.floor((durationMs % 60_000) / 1000)
  return `${minutes}m ${String(seconds).padStart(2, '0')}s`
}

function stripTerminalControls(value) {
  return String(value)
    .replace(/\u001B(?:[@-_]|\[[0-?]*[ -/]*[@-~])/g, '')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
}

function singleLine(value, maxLength = 220) {
  const normalized = stripTerminalControls(value)
    .replace(/\r?\n/g, ' ↩ ')
    .replace(/\s+/g, ' ')
    .trim()

  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, Math.max(0, maxLength - 1))}…`
}

function stableJson(value) {
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function writeLine(symbol, message) {
  if (rawOutput) return

  finishAssistantLine()
  lastOutputAt = Date.now()
  process.stdout.write(`  ${elapsedLabel()}  ${symbol} ${stripTerminalControls(message)}\n`)
}

function writeAssistantDelta(delta) {
  if (rawOutput) return
  const safeDelta = stripTerminalControls(delta).replace(/\r/g, '')
  if (safeDelta.length === 0) return

  lastOutputAt = Date.now()
  const pieces = safeDelta.split(/(\n)/)

  for (const piece of pieces) {
    if (piece === '') continue

    if (!assistantLineOpen) {
      process.stdout.write(`  ${elapsedLabel()}  ℹ `)
      assistantLineOpen = true
    }

    if (piece === '\n') {
      process.stdout.write('\n')
      assistantLineOpen = false
    } else {
      process.stdout.write(piece)
    }
  }
}

function finishAssistantLine() {
  if (!assistantLineOpen) return
  process.stdout.write('\n')
  assistantLineOpen = false
}

function describeShellCommand(command) {
  const detail = singleLine(command ?? '(command missing)', 180)
  const normalized = detail.toLowerCase()

  if (normalized.includes('quality-gate.sh')) return { action: 'Run quality gate', detail }
  if (/^npm (test|run test)(\s|$)/.test(normalized)) return { action: 'Run tests', detail }
  if (/^npm run typecheck(\s|$)/.test(normalized)) return { action: 'Run typecheck', detail }
  if (/^npm run build(\s|$)/.test(normalized)) return { action: 'Build project', detail }
  if (/^git status(\s|$)/.test(normalized)) return { action: 'Inspect repository', detail }
  if (/^git (diff|log|show)(\s|$)/.test(normalized)) return { action: 'Inspect Git changes', detail }
  if (/^(find|fd)(\s|$)/.test(normalized)) return { action: 'Find files', detail }
  if (/^(grep|rg)(\s|$)/.test(normalized)) return { action: 'Search files', detail }

  return { action: 'Run shell command', detail }
}

function describeTool(toolName, args) {
  const safeArgs = args && typeof args === 'object' ? args : {}

  switch (toolName) {
    case 'read': {
      const range = [
        safeArgs.offset !== undefined ? `offset ${safeArgs.offset}` : '',
        safeArgs.limit !== undefined ? `limit ${safeArgs.limit}` : '',
      ].filter(Boolean)
      const path = singleLine(safeArgs.path ?? '(path missing)', 170)
      return { action: 'Read file', detail: `${path}${range.length > 0 ? ` (${range.join(', ')})` : ''}` }
    }
    case 'bash':
      return describeShellCommand(safeArgs.command)
    case 'edit': {
      const editCount = Array.isArray(safeArgs.edits) ? safeArgs.edits.length : undefined
      const path = singleLine(safeArgs.path ?? '(path missing)', 170)
      return {
        action: 'Edit file',
        detail: `${path}${editCount === undefined ? '' : ` (${editCount} replacement${editCount === 1 ? '' : 's'})`}`,
      }
    }
    case 'write': {
      const byteCount = typeof safeArgs.content === 'string' ? Buffer.byteLength(safeArgs.content) : undefined
      const path = singleLine(safeArgs.path ?? '(path missing)', 170)
      return { action: 'Write file', detail: `${path}${byteCount === undefined ? '' : ` (${byteCount} bytes)`}` }
    }
    case 'grep':
      return {
        action: 'Search files',
        detail: singleLine(`${safeArgs.pattern ?? ''}${safeArgs.path ? ` in ${safeArgs.path}` : ''}` || stableJson(safeArgs), 180),
      }
    case 'find':
      return {
        action: 'Find files',
        detail: singleLine(`${safeArgs.pattern ?? ''}${safeArgs.path ? ` in ${safeArgs.path}` : ''}` || stableJson(safeArgs), 180),
      }
    case 'ls':
      return { action: 'List directory', detail: singleLine(safeArgs.path ?? '.', 180) }
    default: {
      const argumentNames = Object.keys(safeArgs)
      const readableName = singleLine(toolName.replace(/[_-]+/g, ' '), 80)
      return {
        action: `Use ${readableName}`,
        detail: argumentNames.length > 0 ? `arguments: ${argumentNames.join(', ')}` : 'no arguments',
      }
    }
  }
}

function toolReference(sequence) {
  return `[${String(sequence).padStart(2, '0')}]`
}

function formatToolActivity(activity) {
  return `${activity.reference} ${activity.action}${activity.detail ? ` — ${activity.detail}` : ''}`
}

function formatToolCompletion(activity) {
  return `${activity.reference} ${activity.action}`
}

function extractResultText(result) {
  if (typeof result === 'string') return result
  if (!result || typeof result !== 'object') return stableJson(result)

  if (typeof result.error === 'string') return result.error
  if (typeof result.message === 'string') return result.message

  if (Array.isArray(result.content)) {
    const text = result.content
      .filter((item) => item && typeof item === 'object' && typeof item.text === 'string')
      .map((item) => item.text)
      .join('\n')
    if (text) return text
  }

  return stableJson(result)
}

function recordAssistantFailure(message) {
  if (!message || message.role !== 'assistant') return
  if (message.stopReason !== 'error' && message.stopReason !== 'aborted') return

  agentFailed = true
  if (agentFailureReported) return

  agentFailureReported = true
  const detail = message.errorMessage || `Agent request ${message.stopReason}`
  writeLine('✕', singleLine(detail, 1200))
}

function handleEvent(event) {
  switch (event.type) {
    case 'agent_start':
      agentStarted = true
      writeLine('•', 'Agent started.')
      break

    case 'message_update': {
      const update = event.assistantMessageEvent
      if (!update || typeof update !== 'object') break

      if (update.type === 'thinking_start' || update.type === 'thinking_delta') break

      if (update.type === 'text_delta' && typeof update.delta === 'string') {
        writeAssistantDelta(update.delta)
      }
      break
    }

    case 'message_end':
      finishAssistantLine()
      recordAssistantFailure(event.message)
      break

    case 'tool_execution_start': {
      const toolName = String(event.toolName || 'tool')
      const toolCallId = String(event.toolCallId || `${toolName}-${Date.now()}`)
      const description = describeTool(toolName, event.args)
      const activity = {
        ...description,
        reference: toolReference(nextToolSequence),
        startedAt: Date.now(),
        toolName,
      }
      nextToolSequence += 1
      activeTools.set(toolCallId, activity)
      writeLine('→', formatToolActivity(activity))
      break
    }

    case 'tool_execution_end': {
      const toolCallId = String(event.toolCallId || '')
      const existingActivity = activeTools.get(toolCallId)
      const toolName = String(event.toolName || existingActivity?.toolName || 'tool')
      const activity = existingActivity ?? {
        ...describeTool(toolName, undefined),
        reference: '[??]',
        startedAt: Date.now(),
        toolName,
      }
      const status = event.isError ? '✕' : '✓'
      writeLine(status, `${formatToolCompletion(activity)} (${durationLabel(activity.startedAt)})`)

      if (event.isError) {
        const detail = singleLine(extractResultText(event.result), 1200)
        if (detail) writeLine('↳', `${activity.reference} ${detail}`)
      }

      activeTools.delete(toolCallId)
      break
    }

    case 'auto_retry_start':
      writeLine('⚠', `Provider retry ${event.attempt}/${event.maxAttempts}: ${singleLine(event.errorMessage ?? 'request failed', 800)}`)
      break

    case 'auto_retry_end':
      writeLine(event.success ? '✓' : '✕', event.success ? 'Provider retry succeeded.' : `Provider retry failed: ${singleLine(event.finalError ?? 'unknown error', 800)}`)
      break

    case 'compaction_start':
      writeLine('•', `Context compaction started (${event.reason ?? 'unknown reason'}).`)
      break

    case 'compaction_end':
      writeLine(event.aborted ? '⚠' : '✓', event.aborted ? 'Context compaction was aborted.' : 'Context compaction finished.')
      if (event.errorMessage) writeLine('↳', singleLine(event.errorMessage, 800))
      break

    case 'agent_end':
      agentEnded = true
      if (Array.isArray(event.messages)) {
        const finalAssistantMessage = [...event.messages].reverse().find((message) => message?.role === 'assistant')
        recordAssistantFailure(finalAssistantMessage)
      }
      writeLine(agentFailed ? '✕' : '✓', agentFailed ? 'Agent finished with an error.' : 'Agent finished.')
      break

    default:
      break
  }
}

const heartbeat = !rawOutput && heartbeatSeconds > 0
  ? setInterval(() => {
      if (!agentStarted || agentEnded) return

      const now = Date.now()
      const idleMs = now - lastOutputAt
      const heartbeatMs = heartbeatSeconds * 1000
      if (idleMs < heartbeatMs || now - lastHeartbeatAt < heartbeatMs) return

      lastHeartbeatAt = now
      if (activeTools.size === 1) {
        const [activity] = activeTools.values()
        writeLine('•', `${formatToolActivity(activity)} is still running…`)
      } else if (activeTools.size > 1) {
        const references = [...activeTools.values()].map((activity) => activity.reference).join(', ')
        writeLine('•', `${activeTools.size} tools are still running: ${references}.`)
      } else {
        writeLine('•', 'Waiting for model…')
      }
    }, Math.min(1000, Math.max(100, heartbeatSeconds * 1000)))
  : undefined

const input = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
})

try {
  for await (const line of input) {
    if (rawOutput) process.stdout.write(`${line}\n`)
    if (line.trim() === '') continue

    try {
      handleEvent(JSON.parse(line))
    } catch (error) {
      rendererFailed = true
      const reason = error instanceof Error ? error.message : String(error)
      if (rawOutput) {
        process.stderr.write(`Could not parse Pi event: ${singleLine(reason, 400)}\n`)
      } else {
        writeLine('⚠', `Could not parse Pi event: ${singleLine(reason, 400)}`)
        writeLine('↳', singleLine(line, 1200))
      }
    }
  }
} catch (error) {
  rendererFailed = true
  const reason = error instanceof Error ? error.message : String(error)
  if (rawOutput) {
    process.stderr.write(`Agent event stream failed: ${singleLine(reason, 800)}\n`)
  } else {
    writeLine('✕', `Agent event stream failed: ${singleLine(reason, 800)}`)
  }
} finally {
  if (heartbeat) clearInterval(heartbeat)
  finishAssistantLine()
}

if (rendererFailed) {
  process.exitCode = 2
} else if (agentFailed) {
  process.exitCode = 1
}
