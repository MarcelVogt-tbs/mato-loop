import { describe, expect, it } from 'vitest'

import { canonicalResearchSourceUrl } from '../src/utils/research-sources'

describe('research sources', () => {
  it('normalizes source URLs for inline citation matching', () => {
    expect(canonicalResearchSourceUrl('HTTPS://EXAMPLE.COM/report#section')).toBe(
      'https://example.com/report'
    )
    expect(canonicalResearchSourceUrl('https://user:secret@example.com/report')).toBeNull()
  })
})
