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
    expect(transcript.messages[1].toolHistory.items[0]).toMatchObject({
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

    const calls = buildChatTranscript(messages, [], { running: true }).messages[1].toolHistory.items

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
    }).messages[1].toolHistory.items

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
    }).messages[1].toolHistory.items

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

    const calls = buildChatTranscript(messages, approvals, { running: true }).messages[1].toolHistory.items

    expect(calls.map((call) => call.active)).toEqual([false, false])
  })

  it('groups every tool round of one user request into one history', () => {
    const messages = [
      { id: 'user-1', role: 'user', content: 'Recherchiere das' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        createdAt: '2026-08-06T12:00:01Z',
        trustLevel: 'private',
        reasoning: { kind: 'summary', text: 'Suche planen', observedDurationMs: 200, reasoningTokens: 10 },
        toolCalls: [{ id: 'call-1', name: 'web_search', arguments: { query: 'MatoLoop' } }]
      },
      { id: 'result-1', role: 'tool', toolCallId: 'call-1', content: 'Treffer' },
      {
        id: 'assistant-2',
        role: 'assistant',
        content: '',
        reasoning: { kind: 'summary', text: 'Treffer prüfen', observedDurationMs: 300, reasoningTokens: 20 },
        toolCalls: [{ id: 'call-2', name: 'webfetch', arguments: { url: 'https://example.com' } }]
      },
      { id: 'result-2', role: 'tool', toolCallId: 'call-2', content: 'Seite' },
      {
        id: 'assistant-3', role: 'assistant', content: 'Ergebnis', toolCalls: [],
        modelName: 'Antwortmodell', trustLevel: 'public', createdAt: '2026-08-06T12:00:03Z'
      }
    ]

    const transcript = buildChatTranscript(messages)

    expect(transcript.messages.map((message) => message.id))
      .toEqual(['user-1', 'assistant-1', 'assistant-2', 'assistant-3'])
    expect(transcript.messages[1].toolHistory.items.map((item) => item.tool.name))
      .toEqual(['web_search', 'webfetch'])
    expect(transcript.messages[2].reasoningHistory).toMatchObject({
      observedDurationMs: 500,
      reasoningTokens: 30
    })
    expect(transcript.messages[2].reasoningHistory.entries.map((item) => item.text))
      .toEqual(['Suche planen', 'Treffer prüfen'])
    expect(transcript.messages[1]).toMatchObject({
      showAgentLabel: true,
      continuesAssistantTurn: true,
      createdAt: null,
      modelName: null,
      trustLevel: null
    })
    expect(transcript.messages[3]).toMatchObject({
      hasAssistantContent: true,
      createdAt: '2026-08-06T12:00:03Z',
      modelName: 'Antwortmodell',
      trustLevel: 'private'
    })
  })

  it('does not mix reused tool call ids across user requests', () => {
    const messages = [
      { id: 'user-1', role: 'user', content: 'Erste Anfrage' },
      {
        id: 'assistant-1', role: 'assistant', content: '',
        toolCalls: [{ id: 'shared', name: 'read_file', arguments: {} }]
      },
      { id: 'result-1', role: 'tool', toolCallId: 'shared', content: 'Erstes Ergebnis' },
      { id: 'user-2', role: 'user', content: 'Zweite Anfrage' },
      {
        id: 'assistant-2', role: 'assistant', content: '',
        toolCalls: [{ id: 'shared', name: 'read_file', arguments: {} }]
      },
      { id: 'result-2', role: 'tool', toolCallId: 'shared', content: 'Zweites Ergebnis' }
    ]

    const histories = buildChatTranscript(messages).messages
      .filter((message) => message.toolHistory)
      .map((message) => message.toolHistory)

    expect(histories[0].items[0].result.content).toBe('Erstes Ergebnis')
    expect(histories[1].items[0].result.content).toBe('Zweites Ergebnis')
  })
})
