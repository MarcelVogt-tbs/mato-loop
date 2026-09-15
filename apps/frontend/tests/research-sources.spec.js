import { describe, expect, it } from 'vitest'

import { canonicalResearchSourceUrl, mergeResearchSources } from '../src/utils/research-sources'

describe('research sources', () => {
  it('normalizes source URLs for inline citation matching', () => {
    expect(canonicalResearchSourceUrl('HTTPS://EXAMPLE.COM/report#section')).toBe(
      'https://example.com/report'
    )
    expect(canonicalResearchSourceUrl('https://user:secret@example.com/report')).toBeNull()
  })

  it('merges citation-eligible live sources by canonical URL', () => {
    expect(mergeResearchSources(
      [{ title: 'Bestehend', url: 'https://example.com/report#alt' }],
      [
        { title: 'Aktuell', url: 'https://example.com/report#neu' },
        { url: 'https://docs.example.org/path' },
        { title: 'Intern', url: 'https://private.example', citationEligible: false }
      ]
    )).toEqual([
      { title: 'Aktuell', url: 'https://example.com/report' },
      { title: 'docs.example.org', url: 'https://docs.example.org/path' }
    ])
  })
})
