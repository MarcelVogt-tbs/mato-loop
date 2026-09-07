import { describe, expect, it } from 'vitest'

import {
  activityDurationMs,
  agentActivityDisplayKey,
  agentActivityLabel,
  agentRunPresentation,
  formatActivityDuration,
  runWarningEntries
} from '../src/utils/agent-activity'

describe('agent activity', () => {
  it('shows a persisted tool-limit handoff without treating it as a verified success', () => {
    const [entry] = runWarningEntries(['agent.toolCallLimitReached'], 'limited')
    expect(entry.type).toBe('warning')
    expect(entry.presentation.title).toBe('Tool-Limit erreicht')
    expect(entry.presentation.summary).toContain('offene Aufgaben')
  })
  it('uses safe German labels for runtime phases', () => {
    expect(agentActivityLabel({ phase: 'preparing_context' }))
      .toBe('Verarbeite Anfrage')
    expect(agentActivityLabel({ phase: 'waiting_model', modelRequest: 1 }))
      .toBe('Verarbeite Anfrage')
    expect(agentActivityLabel({ phase: 'waiting_model', modelRequest: 2 }))
      .toBe('Verarbeite Tool-Ergebnisse')
    expect(agentActivityLabel({
      phase: 'waiting_model', modelRequest: 2, providerAttempt: 2, providerAttemptTotal: 3
    })).toBe('Verarbeite Tool-Ergebnisse')
  })

  it('prefers non-empty backend activity labels for every phase kind', () => {
    expect(agentActivityLabel({
      phase: 'waiting_model', modelRequest: 2, activityLabel: 'Kanonische Modellaktivität'
    })).toBe('Kanonische Modellaktivität')
    expect(agentActivityLabel({
      phase: 'processing_tool', tool: 'read_file', activityLabel: 'Kanonische Tool-Aktivität'
    })).toBe('Kanonische Tool-Aktivität')
    expect(agentActivityLabel({
      phase: 'unknown', activityLabel: 'Kanonische Aktivität'
    })).toBe('Kanonische Aktivität')
    expect(agentActivityLabel({ phase: 'streaming', activityLabel: '   ' }))
      .toBe('Agent arbeitet')
  })

  it('reuses readable tool identities without exposing arguments', () => {
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'read_file' }))
      .toBe('Datei lesen')
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'unknown_tool' }))
      .toBe('Tool ausführen')
    expect(agentActivityLabel({
      phase: 'processing_tool', tool: 'read_file', index: 1, total: 3
    })).toBe('Datei lesen')
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'web_search' }))
      .toBe('Websuche')
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'webfetch' }))
      .toBe('Webseite abrufen')
  })

  it('identifies changes to the visible activity independently from event timestamps', () => {
    expect(agentActivityDisplayKey({ phase: 'waiting_model', modelRequest: 2 }))
      .toBe(agentActivityDisplayKey({ phase: 'waiting_model', modelRequest: 3 }))
    expect(agentActivityDisplayKey({ phase: 'waiting_model', modelRequest: 2 }))
      .toBe(agentActivityDisplayKey({
        phase: 'waiting_model', modelRequest: 2, providerAttempt: 2, providerAttemptTotal: 3
      }))
    expect(agentActivityDisplayKey({
      phase: 'waiting_model', modelRequest: 2, providerAttempt: 2, providerAttemptTotal: 3
    })).toBe(agentActivityDisplayKey({
      phase: 'waiting_model', modelRequest: 2, providerAttempt: 3, providerAttemptTotal: 3
    }))
    expect(agentActivityDisplayKey({ phase: 'processing_tool', tool: 'read_file', index: 0, total: 2 }))
      .not.toBe(agentActivityDisplayKey({ phase: 'processing_tool', tool: 'read_file', index: 1, total: 2 }))
  })

  it('keeps the current activity visible with the most useful summary', () => {
    const activity = { phase: 'waiting_model', modelRequest: 1 }
    expect(agentRunPresentation({ running: true, activity })).toEqual({
      showActivity: true,
      label: 'Verarbeite Anfrage',
      summary: '',
      announcement: 'Verarbeite Anfrage'
    })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'compacting_context' },
      streamContextSummary: {
        content: 'Goal\nWeiterarbeiten\n\nFacts\nDer bisherige Kontext wird verdichtet'
      }
    })).toEqual({
      showActivity: true,
      label: 'Kontext verdichten',
      summary: '',
      announcement: 'Kontext verdichten'
    })
    expect(agentRunPresentation({
      running: true,
      activity,
      streamReasoning: { kind: 'summary', text: '**Ergebnisse prüfen**\nDetails' }
    })).toEqual({
      showActivity: true,
      label: 'Ergebnisse prüfen',
      summary: '',
      announcement: 'Nachdenken'
    })
    expect(agentRunPresentation({
      running: true,
      activity: { ...activity, reasoningLabel: 'Kanonische Denkaktivität' },
      streamReasoning: { kind: 'content', text: 'Weitere Überlegung' }
    })).toEqual({
      showActivity: true,
      label: 'Kanonische Denkaktivität',
      summary: '',
      announcement: 'Nachdenken'
    })
    expect(agentRunPresentation({
      running: true,
      activity,
      streamReasoning: { kind: 'content', text: 'Öffentliche Überlegung' }
    })).toEqual({
      showActivity: true,
      label: 'Nachdenken',
      summary: '',
      announcement: 'Nachdenken'
    })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'streaming' },
      streamReasoning: { kind: 'summary', text: '**Ergebnisse prüfen**\nDetails' },
      streamContent: 'Antwort'
    }))
      .toEqual({
        showActivity: false,
        label: '',
        summary: '',
        announcement: 'Antwort'
      })
    expect(agentRunPresentation({
      running: true,
      activity: {
        phase: 'processing_tool', tool: 'read_file', toolCallId: 'call-1', index: 1, total: 3
      },
      transcript: {
        messages: [{
          workHistory: {
            entries: [{
              type: 'tool', current: true, active: true,
              tool: { id: 'call-1', name: 'read_file', arguments: { path: 'plan.md' } }
            }]
          }
        }],
        unmatchedApprovals: []
      }
    })).toEqual({
      showActivity: true,
      label: 'Datei lesen',
      summary: 'plan.md',
      announcement: 'Datei lesen'
    })
    expect(agentRunPresentation({
      running: true,
      activity,
      transcript: {
        messages: [{ workHistory: { entries: [{
          type: 'tool', approval: { title: 'Datei schreiben?' }
        }] } }],
        unmatchedApprovals: []
      }
    })).toEqual({
      showActivity: true,
      label: 'Freigabe erforderlich',
      summary: 'Datei schreiben?',
      announcement: 'Freigabe erforderlich: Datei schreiben?'
    })
    expect(agentRunPresentation({
      running: true,
      activity,
      transcript: {
        messages: [],
        unmatchedApprovals: [],
        unmatchedQuestions: [{ questions: [{ question: 'Welches Format?' }] }]
      }
    })).toEqual({
      showActivity: true,
      label: 'Antwort erforderlich',
      summary: 'Welches Format?',
      announcement: 'Antwort erforderlich'
    })
    expect(agentRunPresentation({
      running: true,
      stopping: true,
      activity
    })).toEqual({
      showActivity: true,
      label: 'Anfrage abbrechen',
      summary: '',
      announcement: 'Anfrage abbrechen'
    })
    expect(agentRunPresentation({ running: true, activity: { phase: 'finalizing' } })).toEqual({
      showActivity: false,
      label: '',
      summary: '',
      announcement: 'Antwort'
    })
    expect(agentRunPresentation({ running: false, activity })).toEqual({
      showActivity: false,
      label: '',
      summary: '',
      announcement: ''
    })
  })

  it('formats the total duration in whole seconds', () => {
    expect(formatActivityDuration(9_900)).toBe('9 s')
    expect(formatActivityDuration(65_000)).toBe('1 min 5 s')
    expect(formatActivityDuration(7_500_000)).toBe('2 h 5 min')
    expect(formatActivityDuration(183_600_000)).toBe('2 T 3 h')
    expect(formatActivityDuration(0)).toBe('0 s')
  })

  it('adds only the current active segment to persisted runtime', () => {
    const now = Date.parse('2026-07-19T12:00:05.000Z')

    expect(activityDurationMs({ activeDurationMs: 9_000, activeDurationUpdatedAt: null }, true, now))
      .toBe(9_000)
    expect(activityDurationMs({
      activeDurationMs: 9_000,
      activeDurationUpdatedAt: '2026-07-19T12:00:02.000Z'
    }, true, now)).toBe(12_000)
    expect(activityDurationMs({
      activeDurationMs: 9_000,
      activeDurationUpdatedAt: '2026-07-19T12:00:02.000Z'
    }, false, now)).toBe(9_000)
    expect(activityDurationMs({}, false, now)).toBeNull()
  })

  it('maps only known runtime warnings to German activity entries', () => {
    expect(runWarningEntries([
      { code: 'privacy.placeholderRestorationIncomplete', modelRequest: 2 },
      { code: 'provider.unknown', modelRequest: 2 }
    ], 'live', true)).toEqual([{
      key: 'live-2-privacy.placeholderRestorationIncomplete',
      type: 'warning',
      warning: true,
      live: true,
      presentation: {
        title: 'Geschützter Wert nicht verfügbar',
        summary: 'Ein Datenschutz-Platzhalter konnte nicht wiederhergestellt werden.'
      }
    }])
  })
})
