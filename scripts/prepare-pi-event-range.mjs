#!/usr/bin/env node

import { once } from 'node:events'
import { createReadStream, createWriteStream } from 'node:fs'
import { chmod } from 'node:fs/promises'

const MAX_SEGMENT_BYTES = 8_000
const MAX_EVENT_LINE_BYTES = 8 * 1024 * 1024

function usage() {
  process.stderr.write('Usage: prepare-pi-event-range.mjs <input.jsonl> <first-line> <last-line> <output.txt>\n')
}

function parsePositiveInteger(value, label) {
  if (!/^[1-9][0-9]*$/.test(value ?? '')) {
    throw new Error(`${label} must be a positive integer`)
  }
  return Number(value)
}

function messageMetadata(message) {
  if (!message || typeof message !== 'object' || Array.isArray(message)) return message

  const metadata = {}
  for (const key of [
    'role',
    'provider',
    'model',
    'api',
    'stopReason',
    'errorMessage',
    'timestamp',
    'usage',
  ]) {
    if (message[key] !== undefined) metadata[key] = message[key]
  }

  if (Array.isArray(message.content)) {
    metadata.contentSummary = message.content.map((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return typeof item
      return {
        type: item.type,
        ...(typeof item.name === 'string' ? { name: item.name } : {}),
      }
    })
  }

  return metadata
}

function normalizeMessageUpdate(event) {
  const update = event.assistantMessageEvent
  if (!update || typeof update !== 'object' || Array.isArray(update)) {
    return { type: event.type, assistantMessageEvent: update }
  }

  const normalizedUpdate = {}
  for (const [key, value] of Object.entries(update)) {
    if (key === 'partial') continue
    normalizedUpdate[key] = value
  }

  return {
    type: event.type,
    assistantMessageEvent: normalizedUpdate,
  }
}

function normalizeEvent(event) {
  if (!event || typeof event !== 'object' || Array.isArray(event)) return event

  switch (event.type) {
    case 'message_update':
      return normalizeMessageUpdate(event)

    case 'message_start':
      return {
        type: event.type,
        message: event.message?.role === 'user'
          ? event.message
          : messageMetadata(event.message),
      }

    case 'message_end':
      return {
        type: event.type,
        message: messageMetadata(event.message),
      }

    case 'tool_execution_update':
      return {
        type: event.type,
        toolCallId: event.toolCallId,
        toolName: event.toolName,
      }

    case 'turn_end':
      return {
        type: event.type,
        message: messageMetadata(event.message),
        ...(Array.isArray(event.toolResults)
          ? { toolResultCount: event.toolResults.length }
          : {}),
      }

    case 'agent_end': {
      const messages = Array.isArray(event.messages) ? event.messages : []
      const finalAssistantMessage = [...messages]
        .reverse()
        .find((message) => message?.role === 'assistant')

      return {
        type: event.type,
        ...(event.willRetry !== undefined ? { willRetry: event.willRetry } : {}),
        messageCount: messages.length,
        ...(finalAssistantMessage
          ? { finalAssistantMessage: messageMetadata(finalAssistantMessage) }
          : {}),
      }
    }

    default:
      return event
  }
}

function projectEventLine(line, lineNumber) {
  if (line.length === 0) {
    return JSON.stringify({
      type: 'monitor_projection_warning',
      sourceLine: lineNumber,
      reason: 'empty-event-line',
    })
  }

  try {
    return JSON.stringify(normalizeEvent(JSON.parse(line.toString('utf8'))))
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    return JSON.stringify({
      type: 'monitor_projection_warning',
      sourceLine: lineNumber,
      reason: 'invalid-json',
      sourceBytes: line.length,
      detail: reason.slice(0, 400),
    })
  }
}

function splitAtUtf8Boundaries(value, maxBytes) {
  const bytes = Buffer.from(value)
  if (bytes.length === 0) return ['']

  const segments = []
  let offset = 0

  while (offset < bytes.length) {
    let end = Math.min(offset + maxBytes, bytes.length)
    while (end < bytes.length && end > offset && (bytes[end] & 0xc0) === 0x80) {
      end -= 1
    }
    if (end === offset) end = Math.min(offset + maxBytes, bytes.length)

    segments.push(bytes.subarray(offset, end).toString('utf8'))
    offset = end
  }

  return segments
}

async function writeText(stream, value) {
  if (!stream.write(value)) await once(stream, 'drain')
}

async function writeProjectedLine(stream, lineNumber, projectedLine) {
  const segments = splitAtUtf8Boundaries(projectedLine, MAX_SEGMENT_BYTES)
  for (let index = 0; index < segments.length; index += 1) {
    await writeText(
      stream,
      `--- PI EVENT LINE ${lineNumber} SEGMENT ${index + 1}/${segments.length} ---\n${segments[index]}\n`,
    )
  }
}

async function projectRange(inputPath, firstLine, lastLine, outputPath) {
  const input = createReadStream(inputPath)
  const output = createWriteStream(outputPath, { flags: 'w', mode: 0o600 })
  let lineNumber = 1
  let selectedLineBytes = 0
  let selectedLineChunks = []
  let selectedLineOversized = false
  let completedLines = 0

  const collect = (chunk) => {
    if (lineNumber < firstLine || lineNumber > lastLine || chunk.length === 0) return

    selectedLineBytes += chunk.length
    if (selectedLineOversized) return
    if (selectedLineBytes > MAX_EVENT_LINE_BYTES) {
      selectedLineOversized = true
      selectedLineChunks = []
      return
    }
    selectedLineChunks.push(Buffer.from(chunk))
  }

  const finishLine = async () => {
    completedLines = lineNumber
    if (lineNumber >= firstLine && lineNumber <= lastLine) {
      const projectedLine = selectedLineOversized
        ? JSON.stringify({
            type: 'monitor_projection_warning',
            sourceLine: lineNumber,
            reason: 'event-line-exceeds-safe-projection-limit',
            sourceBytes: selectedLineBytes,
            maxBytes: MAX_EVENT_LINE_BYTES,
          })
        : projectEventLine(Buffer.concat(selectedLineChunks, selectedLineBytes), lineNumber)

      await writeProjectedLine(output, lineNumber, projectedLine)
    }

    lineNumber += 1
    selectedLineBytes = 0
    selectedLineChunks = []
    selectedLineOversized = false
  }

  await writeText(output, [
    '--- NORMALIZED PI EVENT VIEW ---',
    `SOURCE RANGE: lines ${firstLine} through ${lastLine} inclusive.`,
    'Cumulative message/tool-call snapshots are reduced to incremental deltas and metadata.',
    'Authoritative tool starts, arguments, completions, results, retries, failures, and lifecycle events are retained.',
    '',
  ].join('\n'))

  readLoop:
  for await (const chunk of input) {
    let offset = 0
    while (offset < chunk.length) {
      const newline = chunk.indexOf(0x0a, offset)
      if (newline === -1) {
        collect(chunk.subarray(offset))
        break
      }

      collect(chunk.subarray(offset, newline))
      await finishLine()
      offset = newline + 1

      if (lineNumber > lastLine) {
        input.destroy()
        break readLoop
      }
    }
  }

  if (completedLines < lastLine) {
    throw new Error(`requested line ${lastLine}, but the event stream has only ${completedLines} complete lines`)
  }

  output.end()
  await once(output, 'close')
  await chmod(outputPath, 0o600)
}

async function main() {
  if (process.argv.length !== 6) {
    usage()
    process.exitCode = 2
    return
  }

  const [, , inputPath, firstValue, lastValue, outputPath] = process.argv
  const firstLine = parsePositiveInteger(firstValue, 'first-line')
  const lastLine = parsePositiveInteger(lastValue, 'last-line')
  if (lastLine < firstLine) {
    throw new Error('last-line must be greater than or equal to first-line')
  }

  await projectRange(inputPath, firstLine, lastLine, outputPath)
}

main().catch((error) => {
  const reason = error instanceof Error ? error.message : String(error)
  process.stderr.write(`Could not prepare Pi event range: ${reason}\n`)
  process.exitCode = 1
})
