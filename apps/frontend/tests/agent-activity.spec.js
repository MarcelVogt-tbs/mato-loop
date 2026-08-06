import { describe, expect, it } from 'vitest'

import {
  agentActivityDisplayKey,
  agentActivityLabel,
  agentRunPresentation,
  formatActivityDuration
} from '../src/utils/agent-activity'

describe('agent activity', () => {
  it('uses safe German labels for runtime phases', () => {
    expect(agentActivityLabel({ phase: 'preparing_context' }))
      .toBe('Kontext und Tools werden vorbereitet')
    expect(agentActivityLabel({ phase: 'waiting_model', modelRequest: 1 }))
      .toBe('Modell analysiert die Anfrage')
    expect(agentActivityLabel({ phase: 'waiting_model', modelRequest: 2 }))
      .toBe('Modell wertet Tool-Ergebnisse aus')
    expect(agentActivityLabel({ phase: 'streaming' })).toBe('Antwort wird erstellt')
  })

  it('reuses readable tool identities without exposing arguments', () => {
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'read_file' }))
      .toBe('Tool wird ausgeführt: Datei lesen')
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'unknown_tool' }))
      .toBe('Tool wird ausgeführt: Tool ausführen')
    expect(agentActivityLabel({
      phase: 'processing_tool', tool: 'read_file', index: 1, total: 3
    })).toBe('Tool wird ausgeführt: Datei lesen (2 von 3)')
  })

  it('identifies changes to the visible activity independently from event timestamps', () => {
    expect(agentActivityDisplayKey({ phase: 'waiting_model', modelRequest: 2 }))
      .toBe(agentActivityDisplayKey({ phase: 'waiting_model', modelRequest: 3 }))
    expect(agentActivityDisplayKey({ phase: 'processing_tool', tool: 'read_file', index: 0, total: 2 }))
      .not.toBe(agentActivityDisplayKey({ phase: 'processing_tool', tool: 'read_file', index: 1, total: 2 }))
  })

  it('shows the global activity only when no concrete run artifact owns the status', () => {
    const activity = { phase: 'waiting_model', modelRequest: 1 }
    expect(agentRunPresentation({ running: true, activity })).toEqual({
      showActivity: true,
      announcement: 'Modell analysiert die Anfrage'
    })
    expect(agentRunPresentation({ running: true, activity, streamReasoning: { text: 'Plan' } }))
      .toEqual({ showActivity: false, announcement: 'Denkprozess' })
    expect(agentRunPresentation({ running: true, activity, streamContent: 'Antwort' }))
      .toEqual({ showActivity: false, announcement: 'Antwort wird erstellt' })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'waiting_model', modelRequest: 2 },
      transcript: {
        messages: [{ toolHistory: { items: [{ current: true, result: {} }] } }],
        unmatchedApprovals: []
      }
    })).toEqual({
      showActivity: true,
      announcement: 'Modell wertet Tool-Ergebnisse aus'
    })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'waiting_model', modelRequest: 2 },
      streamReasoning: { text: 'Ergebnisse prüfen' },
      transcript: {
        messages: [{ toolHistory: { items: [{ current: true, result: {} }] } }],
        unmatchedApprovals: []
      }
    })).toEqual({ showActivity: false, announcement: 'Denkprozess' })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'processing_tool', tool: 'read_file' },
      transcript: {
        messages: [{ toolHistory: { items: [{ active: true }] } }],
        unmatchedApprovals: []
      }
    })).toEqual({ showActivity: false, announcement: 'Tool wird ausgeführt: Datei lesen' })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'preparing_tools' },
      transcript: {
        messages: [{ toolHistory: { items: [{ current: true, result: null }] } }],
        unmatchedApprovals: []
      }
    })).toEqual({ showActivity: false, announcement: 'Tool-Aufrufe werden vorbereitet' })
    expect(agentRunPresentation({
      running: true,
      activity,
      transcript: {
        messages: [{ toolHistory: { items: [{ approval: { title: 'Datei schreiben?' } }] } }],
        unmatchedApprovals: []
      }
    })).toEqual({ showActivity: false, announcement: 'Freigabe erforderlich: Datei schreiben?' })
    expect(agentRunPresentation({ running: true, activity: { phase: 'finalizing' } }))
      .toEqual({ showActivity: false, announcement: 'Antwort wird abgeschlossen' })
    expect(agentRunPresentation({ running: false, activity })).toEqual({
      showActivity: false,
      announcement: ''
    })
  })

  it('formats the total duration in whole seconds', () => {
    const startedAt = '2026-07-19T12:00:00.000Z'
    expect(formatActivityDuration(startedAt, Date.parse('2026-07-19T12:00:09.900Z'))).toBe('9 s')
    expect(formatActivityDuration(startedAt, Date.parse('2026-07-19T12:01:05.000Z'))).toBe('1 min 5 s')
    expect(formatActivityDuration(null)).toBe('0 s')
  })
})
