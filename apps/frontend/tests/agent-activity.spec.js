import { describe, expect, it } from 'vitest'

import { agentActivityLabel, formatActivityDuration } from '../src/utils/agent-activity'

describe('agent activity', () => {
  it('uses safe German labels for runtime phases', () => {
    expect(agentActivityLabel({ phase: 'preparing_context' })).toBe('LLM verarbeitet die Anfrage')
    expect(agentActivityLabel({ phase: 'waiting_model', modelRequest: 1 }))
      .toBe('LLM verarbeitet die Anfrage')
    expect(agentActivityLabel({ phase: 'waiting_model', modelRequest: 2 }))
      .toBe('Modell verarbeitet die Tool-Ergebnisse')
    expect(agentActivityLabel({ phase: 'streaming' })).toBe('Antwort wird erstellt')
  })

  it('reuses readable tool identities without exposing arguments', () => {
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'read_file' }))
      .toBe('Tool wird ausgeführt: Datei lesen')
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'unknown_tool' }))
      .toBe('Tool wird ausgeführt: Tool ausführen')
  })

  it('formats the total duration in whole seconds', () => {
    const startedAt = '2026-07-19T12:00:00.000Z'
    expect(formatActivityDuration(startedAt, Date.parse('2026-07-19T12:00:09.900Z'))).toBe('9 s')
    expect(formatActivityDuration(startedAt, Date.parse('2026-07-19T12:01:05.000Z'))).toBe('1 min 5 s')
    expect(formatActivityDuration(null)).toBe('0 s')
  })
})
