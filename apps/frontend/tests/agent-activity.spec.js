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
      .toBe('Verarbeite Anfrage')
    expect(agentActivityLabel({
      phase: 'waiting_model', modelRequest: 2, providerAttempt: 2, providerAttemptTotal: 3
    })).toBe('Verarbeite Anfrage')
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
    expect(agentActivityLabel({
      phase: 'processing_tool', tool: 'custom',
      toolDisplay: { group: 'integrations', icon: 'extension', label: 'Spezialaktion' }
    })).toBe('Spezialaktion')
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
    expect(agentRunPresentation({ running: true, activity })).toMatchObject({
      showActivity: true,
      label: 'Verarbeite Anfrage',
      summary: '',
      announcement: 'Verarbeite Anfrage',
      entry: { presentation: { icon: 'settings' } }
    })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'compacting_context' },
      streamContextSummary: {
        content: 'Goal\nWeiterarbeiten\n\nFacts\nDer bisherige Kontext wird verdichtet'
      }
    })).toMatchObject({
      showActivity: true,
      label: 'Kontext verdichten',
      summary: '',
      announcement: 'Kontext verdichten',
      entry: { presentation: { icon: 'compress' } }
    })
    expect(agentRunPresentation({
      running: true,
      activity,
      streamReasoning: { kind: 'summary', text: '**Ergebnisse prüfen**\nDetails' }
    })).toMatchObject({
      showActivity: true,
      label: 'Nachdenken',
      summary: 'Ergebnisse prüfen',
      announcement: 'Nachdenken',
      entry: { presentation: { icon: 'psychology' } }
    })
    expect(agentRunPresentation({
      running: true,
      activity: { ...activity, reasoningLabel: 'Kanonische Denkaktivität' },
      streamReasoning: { kind: 'content', text: 'Weitere Überlegung' }
    })).toMatchObject({
      showActivity: true,
      label: 'Nachdenken',
      summary: 'Kanonische Denkaktivität',
      announcement: 'Nachdenken'
    })
    expect(agentRunPresentation({
      running: true,
      activity,
      streamReasoning: { kind: 'content', text: 'Öffentliche Überlegung' }
    })).toMatchObject({
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
      .toMatchObject({
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
    })).toMatchObject({
      showActivity: true,
      label: 'Datei lesen',
      summary: 'plan.md',
      announcement: 'Datei lesen',
      tool: { id: 'call-1', name: 'read_file', arguments: { path: 'plan.md' } }
    })
    expect(agentRunPresentation({
      running: true,
      activity,
      transcript: {
        messages: [{ workHistory: { entries: [{
          type: 'tool', tool: { id: 'write', name: 'write_file' }, approval: { title: 'Datei schreiben?' }
        }] } }],
        unmatchedApprovals: []
      }
    })).toMatchObject({
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
    })).toMatchObject({
      showActivity: true,
      label: 'Antwort erforderlich',
      summary: 'Welches Format?',
      announcement: 'Antwort erforderlich'
    })
    expect(agentRunPresentation({
      running: true,
      stopping: true,
      activity
    })).toMatchObject({
      showActivity: true,
      label: 'Anfrage abbrechen',
      summary: '',
      announcement: 'Anfrage abbrechen'
    })
    expect(agentRunPresentation({ running: true, activity: { phase: 'finalizing' } })).toMatchObject({
      showActivity: false,
      label: '',
      summary: '',
      announcement: 'Antwort'
    })
    expect(agentRunPresentation({ running: false, activity })).toMatchObject({
      showActivity: false,
      label: '',
      summary: '',
      announcement: ''
    })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'waiting_model', modelRequest: 2 }
    })).toMatchObject({ showActivity: true, label: 'Verarbeite Anfrage' })
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'waiting_model', modelRequest: 2 },
      streamReasoning: { kind: 'content', text: 'Öffentliche Überlegung' }
    }).showActivity).toBe(true)
    expect(agentRunPresentation({
      running: true,
      activity: { phase: 'waiting_model', modelRequest: 2 },
      streamContextSummary: { content: 'Goal\nWeiterarbeiten' }
    }).showActivity).toBe(true)
    expect(agentRunPresentation({
      running: true,
      activity: {
        phase: 'processing_tool', tool: 'browser_navigate',
        toolDisplay: {
          group: 'web', icon: 'language', label: 'Website aufrufen',
          summary: 'https://example.com'
        }
      }
    })).toMatchObject({
      label: 'Website aufrufen', summary: 'https://example.com',
      tool: { name: 'browser_navigate', toolDisplay: { icon: 'language' } }
    })
  })

  it('formats the total duration in whole seconds', () => {
    expect(formatActivityDuration(9_900)).toBe('9 s')
    expect(formatActivityDuration(65_000)).toBe('1 min 5 s')
    expect(formatActivityDuration(7_500_000)).toBe('2 h 5 min')
    expect(formatActivityDuration(183_600_000)).toBe('2 T 3 h')
    expect(formatActivityDuration(0)).toBe('0 s')
  })

  it('keeps completed compaction in history instead of selecting it during later model waits', () => {
    const completedSummary = {
      key: 'summary', type: 'context-summary', current: true,
      text: 'Completed summary', presentation: { title: 'Gesprächskontext verdichtet', icon: 'compress' }
    }
    const transcript = { messages: [{ workHistory: { entries: [completedSummary] } }] }
    for (const modelRequest of [2, 3]) {
      const presentation = agentRunPresentation({
        running: true, activity: { phase: 'waiting_model', modelRequest }, transcript,
        streamContextSummary: { content: 'Stale live summary' }
      })
      expect(presentation.entry.type).toBe('reasoning')
      expect(presentation.entry.presentation.title).toBe('Verarbeite Anfrage')
    }
    expect(transcript.messages[0].workHistory.entries).toEqual([completedSummary])
    expect(agentRunPresentation({
      running: true, activity: { phase: 'compacting_context' },
      streamContextSummary: { content: 'New summary' }, transcript
    }).entry).toMatchObject({
      type: 'context-summary', text: 'New summary',
      presentation: { title: 'Gesprächskontext wird verdichtet' }
    })
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
