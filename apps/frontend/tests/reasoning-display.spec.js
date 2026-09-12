import { describe, expect, it } from 'vitest'

import {
  formatReasoningDuration,
  reasoningBlocks,
  reasoningHistoryPresentation,
  reasoningStatusLabel
} from '../src/utils/reasoning-display'

describe('reasoning display', () => {
  it('formats observed durations without inventing missing values', () => {
    expect(formatReasoningDuration(640)).toBe('640 ms')
    expect(formatReasoningDuration(1600)).toBe('1.6 s')
    expect(formatReasoningDuration(null)).toBeNull()
  })

  it('renders adjacent Codex summary headings as safe separated blocks', () => {
    expect(reasoningBlocks({
      kind: 'summary',
      text: '**Planung****Prüfung**\nDetails'
    })).toEqual([
      [{ text: 'Planung', strong: true }],
      [
        { text: 'Prüfung', strong: true },
        { text: '\nDetails', strong: false }
      ]
    ])
  })

  it('keeps public reasoning content as literal text', () => {
    expect(reasoningBlocks({ kind: 'content', text: '**Keine Formatierung**' }))
      .toEqual([[{ text: '**Keine Formatierung**', strong: false }]])
  })

  it('uses the latest Codex summary heading as a status', () => {
    expect(reasoningStatusLabel({
      kind: 'summary',
      text: '**Planung**\nDetails\n\n**Ergebnisse prüfen**\nWeitere Details'
    })).toBe('Ergebnisse prüfen')
  })

  it('cleans partial summary markers and ignores reasoning content', () => {
    expect(reasoningStatusLabel({ kind: 'summary', text: '**Wochenmenü planen' }))
      .toBe('Wochenmenü planen')
    expect(reasoningStatusLabel({ kind: 'content', text: 'Interne Überlegung' })).toBeNull()
  })

  it('keeps provider summaries visible while reasoning details stay optional', () => {
    expect(reasoningHistoryPresentation({
      kind: 'summary',
      text: '**Ergebnisse prüfen**\nDetails',
      observedDurationMs: 1600
    })).toEqual({
      title: 'Überlegt',
      summary: 'Ergebnisse prüfen',
      duration: '1.6 s'
    })
    expect(reasoningHistoryPresentation({ kind: 'content', text: 'Öffentliche Überlegung' }, true))
      .toEqual({ title: 'Modell überlegt', summary: null, duration: null })
    expect(reasoningHistoryPresentation({ status: 'interrupted' }))
      .toEqual({ title: 'Überlegung unterbrochen', summary: null, duration: null })
  })
})
