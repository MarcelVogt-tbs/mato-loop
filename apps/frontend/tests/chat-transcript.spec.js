import { describe, expect, it } from 'vitest'

import {
  buildChatTranscript,
  groupWorkHistoryEntries,
  retryAvailableForMessage,
  splitLiveWorkHistory
} from '../src/utils/chat-transcript'

describe('chat transcript', () => {
  it('offers retry only for the current backend-approved failed response', () => {
    const error = {
      id: 'error-1', role: 'assistant', kind: 'runtime_error', retryAvailable: true
    }

    expect(retryAvailableForMessage(error, [{ id: 'user-1' }, error])).toBe(true)
    expect(retryAvailableForMessage(error, [error, { id: 'user-2' }])).toBe(false)
    expect(retryAvailableForMessage(error, [error], true)).toBe(false)
    expect(retryAvailableForMessage({ ...error, retryAvailable: false }, [error])).toBe(false)
  })

  it('groups consecutive filesystem context tools without crossing other activity', () => {
    const read = { key: 'read', type: 'tool', tool: { name: 'read_file' } }
    const search = { key: 'search', type: 'tool', tool: { name: 'grep' } }
    const note = { key: 'note', type: 'note', text: 'Bearbeitung beginnt' }
    const list = { key: 'list', type: 'tool', tool: { name: 'list_directory' } }

    expect(groupWorkHistoryEntries([read, search, note, list])).toEqual([
      { key: 'context-read', type: 'context', items: [read, search] },
      note,
      { key: 'context-list', type: 'context', items: [list] }
    ])
  })

  it('separates the current live step from completed steps and pending calls', () => {
    const completed = {
      key: 'completed', type: 'tool', tool: { id: 'call-1', name: 'web_search' }, result: {}
    }
    const active = {
      key: 'active', type: 'tool', tool: { id: 'call-2', name: 'webfetch' }, active: true
    }
    const pending = {
      key: 'pending', type: 'tool', tool: { id: 'call-3', name: 'read_file' }
    }
    const reasoning = {
      key: 'live-reasoning', type: 'reasoning', live: true, reasoning: { text: 'Prüfen' }
    }

    expect(splitLiveWorkHistory(
      [completed, active, pending],
      { phase: 'processing_tool', toolCallId: 'call-2' }
    )).toEqual({ current: active, previous: [completed] })
    expect(splitLiveWorkHistory(
      [completed, reasoning],
      { phase: 'waiting_model' }
    )).toEqual({ current: reasoning, previous: [completed] })
  })

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
    expect(transcript.messages[1].workHistory.entries[0]).toMatchObject({
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

  it('attaches pending questions to their tool call', () => {
    const messages = [
      { id: 'user-1', role: 'user', content: 'Plane den Ausflug' },
      {
        id: 'assistant-1', role: 'assistant', content: '',
        toolCalls: [{ id: 'call-1', name: 'question', arguments: {} }]
      }
    ]
    const question = { id: 'question-1', toolCallId: 'call-1', questions: [] }

    const transcript = buildChatTranscript(messages, [], { running: true, questions: [question] })

    expect(transcript.messages[1].workHistory.entries[0]).toMatchObject({
      question,
      active: false
    })
    expect(transcript.unmatchedQuestions).toEqual([])
  })

  it('marks only the next unresolved sequential call as running', () => {
    const messages = [
      {
        id: 'user-1', role: 'user', content: 'Arbeite die Schritte ab',
        createdAt: '2026-08-06T12:00:00Z'
      },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '',
        createdAt: '2026-08-06T12:00:01Z',
        toolCalls: [
          { id: 'call-1', name: 'read_file', arguments: { path: 'eins.md' } },
          { id: 'call-2', name: 'read_file', arguments: { path: 'zwei.md' } }
        ]
      }
    ]

    const history = buildChatTranscript(messages, [], { running: true })
      .messages[1].workHistory
    const calls = history.entries

    expect(calls.map((call) => call.active)).toEqual([true, false])
    expect(history).toMatchObject({
      startedAt: '2026-08-06T12:00:00Z',
      completedAt: null
    })
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
    }).messages[1].workHistory.entries

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
    }).messages[1].workHistory.entries

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

    const calls = buildChatTranscript(messages, approvals, { running: true })
      .messages[1].workHistory.entries

    expect(calls.map((call) => call.active)).toEqual([false, false])
  })

  it('groups reasoning and tools chronologically into one work history', () => {
    const messages = [
      {
        id: 'user-1', role: 'user', content: 'Recherchiere das',
        createdAt: '2026-08-06T12:00:00Z'
      },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'Ich starte mit einer Suche.',
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
      .toEqual(['user-1', 'assistant-1', 'assistant-3'])
    expect(transcript.messages[1].workHistory).toMatchObject({
      observedDurationMs: 500,
      startedAt: '2026-08-06T12:00:00Z',
      completedAt: '2026-08-06T12:00:03Z'
    })
    expect(transcript.messages[1].workHistory.entries.map((entry) => (
      entry.reasoning?.text || entry.text || entry.tool.name
    ))).toEqual([
      'Suche planen',
      'Ich starte mit einer Suche.',
      'web_search',
      'Treffer prüfen',
      'webfetch'
    ])
    expect(transcript.messages[1]).toMatchObject({
      showAgentLabel: true,
      continuesAssistantTurn: true,
      content: '',
      createdAt: null,
      modelName: null,
      trustLevel: null
    })
    expect(transcript.messages[2]).toMatchObject({
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
      .filter((message) => message.workHistory)
      .map((message) => message.workHistory)

    expect(histories[0].entries[0].result.content).toBe('Erstes Ergebnis')
    expect(histories[1].entries[0].result.content).toBe('Zweites Ergebnis')
  })
})
