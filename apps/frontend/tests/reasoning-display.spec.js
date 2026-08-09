import { describe, expect, it } from 'vitest'

import {
  formatReasoningDuration,
  reasoningBlocks,
  reasoningStatusLabel,
  reasoningTitle
} from '../src/utils/reasoning-display'

describe('reasoning display', () => {
  it('distinguishes provider summaries from public reasoning content', () => {
    expect(reasoningTitle({ kind: 'summary' })).toBe('Zusammenfassung')
    expect(reasoningTitle({ kind: 'content' })).toBe('Überlegung')
    expect(reasoningTitle({}, true, 'Überlegt')).toBe('Überlegt')
  })

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
})
