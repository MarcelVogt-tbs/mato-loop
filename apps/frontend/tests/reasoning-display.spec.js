import { describe, expect, it } from 'vitest'

import { formatReasoningDuration, reasoningTitle } from '../src/utils/reasoning-display'

describe('reasoning display', () => {
  it('distinguishes provider summaries from public reasoning content', () => {
    expect(reasoningTitle({ kind: 'summary' })).toBe('Begründungszusammenfassung anzeigen')
    expect(reasoningTitle({ kind: 'content' })).toBe('Denkprozess anzeigen')
    expect(reasoningTitle({}, true, 'Denkprozess')).toBe('Denkprozess')
  })

  it('formats observed durations without inventing missing values', () => {
    expect(formatReasoningDuration(640)).toBe('640 ms')
    expect(formatReasoningDuration(1600)).toBe('1.6 s')
    expect(formatReasoningDuration(null)).toBeNull()
  })
})
