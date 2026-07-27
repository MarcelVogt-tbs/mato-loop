import { describe, expect, it } from 'vitest'

import { buildChatTranscript } from '../src/utils/chat-transcript'

describe('chat transcript', () => {
  it('attaches results and approvals to their tool calls', () => {
    const messages = [
      { id: 'user-1', role: 'user', content: 'Lies die Datei' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        toolCalls: [{ id: 'call-1', name: 'read_file', arguments: { path: 'plan.md' } }]
      },
      { id: 'result-1', role: 'tool', toolCallId: 'call-1', content: 'Inhalt' }
    ]
    const approvals = [{ id: 'approval-1', details: { toolCallId: 'call-1' } }]

    const transcript = buildChatTranscript(messages, approvals, { running: true })

    expect(transcript.messages).toHaveLength(2)
    expect(transcript.messages[1].toolCalls[0]).toMatchObject({
      result: messages[2],
      approval: approvals[0],
      active: false
    })
    expect(transcript.unmatchedApprovals).toEqual([])
  })

  it('keeps approvals without a matching visible call as fallback cards', () => {
    const approval = { id: 'approval-1', details: { toolCallId: 'missing' } }
    expect(buildChatTranscript([], [approval]).unmatchedApprovals).toEqual([approval])
  })

  it('marks only the next unresolved sequential call as running', () => {
    const messages = [
      { id: 'user-1', role: 'user', content: 'Arbeite die Schritte ab' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        toolCalls: [
          { id: 'call-1', name: 'read_file', arguments: { path: 'eins.md' } },
          { id: 'call-2', name: 'read_file', arguments: { path: 'zwei.md' } }
        ]
      }
    ]

    const calls = buildChatTranscript(messages, [], { running: true }).messages[1].toolCalls

    expect(calls.map((call) => call.active)).toEqual([true, false])
  })

  it('uses the runtime tool call id instead of stale result ordering', () => {
    const messages = [
      { id: 'user-1', role: 'user', content: 'Arbeite die Schritte ab' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        toolCalls: [
          { id: 'call-1', name: 'read_file', arguments: { path: 'eins.md' } },
          { id: 'call-2', name: 'read_file', arguments: { path: 'zwei.md' } }
        ]
      }
    ]

    const calls = buildChatTranscript(messages, [], {
      running: true,
      activeToolCallId: 'call-2'
    }).messages[1].toolCalls

    expect(calls.map((call) => call.active)).toEqual([false, true])
  })

  it('does not keep a stale unresolved call active during model processing', () => {
    const messages = [
      { id: 'user-1', role: 'user', content: 'Lies die Datei' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        toolCalls: [{ id: 'call-1', name: 'read_file', arguments: { path: 'eins.md' } }]
      }
    ]

    const calls = buildChatTranscript(messages, [], {
      running: true,
      toolExecutionActive: false
    }).messages[1].toolCalls

    expect(calls[0].active).toBe(false)
  })

  it('does not mark calls after an open approval as running', () => {
    const messages = [
      { id: 'user-1', role: 'user', content: 'Arbeite die Schritte ab' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        toolCalls: [
          { id: 'call-1', name: 'write_file', arguments: { path: 'eins.md' } },
          { id: 'call-2', name: 'read_file', arguments: { path: 'zwei.md' } }
        ]
      }
    ]
    const approvals = [{ id: 'approval-1', details: { toolCallId: 'call-1' } }]

    const calls = buildChatTranscript(messages, approvals, { running: true }).messages[1].toolCalls

    expect(calls.map((call) => call.active)).toEqual([false, false])
  })
})
