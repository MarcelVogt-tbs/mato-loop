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
  it('uses safe German labels for runtime phases', () => {
    expect(agentActivityLabel({ phase: 'preparing_context' }))
      .toBe('Kontext und Tools werden vorbereitet')
    expect(agentActivityLabel({ phase: 'waiting_model', modelRequest: 1 }))
      .toBe('Modell analysiert die Anfrage')
    expect(agentActivityLabel({ phase: 'waiting_model', modelRequest: 2 }))
      .toBe('Modell wertet Tool-Ergebnisse aus')
    expect(agentActivityLabel({
      phase: 'waiting_model', modelRequest: 2, providerAttempt: 2, providerAttemptTotal: 3
    })).toBe('Provider-Anfrage: Versuch 2 von 3')
    expect(agentActivityLabel({ phase: 'streaming' })).toBe('Antwort wird erstellt')
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
      .toBe('Antwort wird erstellt')
  })

  it('reuses readable tool identities without exposing arguments', () => {
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'read_file' }))
      .toBe('Datei wird gelesen')
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'unknown_tool' }))
      .toBe('Tool wird ausgeführt')
    expect(agentActivityLabel({
      phase: 'processing_tool', tool: 'read_file', index: 1, total: 3
    })).toBe('Datei wird gelesen')
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'web_search' }))
      .toBe('Web wird durchsucht')
    expect(agentActivityLabel({ phase: 'processing_tool', tool: 'webfetch' }))
      .toBe('Webseite wird abgerufen')
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
      label: 'Modell analysiert die Anfrage',
      summary: '',
      announcement: 'Modell analysiert die Anfrage'
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
      streamReasoning: { kind: 'summary', text: '**Lokale Denkaktivität**' }
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
        showActivity: true,
        label: 'Antwort wird erstellt',
        summary: '',
        announcement: 'Antwort wird erstellt'
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
      label: 'Datei wird gelesen',
      summary: 'plan.md',
      announcement: 'Datei wird gelesen'
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
      label: 'Wartet auf Freigabe',
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
      label: 'Wartet auf deine Antwort',
      summary: 'Welches Format?',
      announcement: 'Antwort erforderlich'
    })
    expect(agentRunPresentation({
      running: true,
      stopping: true,
      activity
    })).toEqual({
      showActivity: true,
      label: 'Anfrage wird abgebrochen',
      summary: '',
      announcement: 'Anfrage wird abgebrochen'
    })
    expect(agentRunPresentation({ running: true, activity: { phase: 'finalizing' } })).toEqual({
      showActivity: true,
      label: 'Antwort wird abgeschlossen',
      summary: '',
      announcement: 'Antwort wird abgeschlossen'
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
