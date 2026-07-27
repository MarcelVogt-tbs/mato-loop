import { describe, expect, it } from 'vitest'

import {
  canonicalResearchSourceUrl,
  linkedResearchSources
} from '../src/utils/research-sources'

describe('research sources', () => {
  it('keeps absolute web links and provides a title fallback', () => {
    expect(linkedResearchSources({
      linkedSources: [
        { title: 'Bericht', url: 'https://example.com/report' },
        { title: ' ', url: 'http://example.org' }
      ]
    })).toEqual([
      { title: 'Bericht', url: 'https://example.com/report' },
      { title: 'example.org', url: 'http://example.org/' }
    ])
  })

  it('rejects unsafe and malformed links', () => {
    expect(linkedResearchSources({
      linkedSources: [
        { title: 'Unsicher', url: 'javascript:alert(1)' },
        { title: 'Relativ', url: '/report' },
        { title: 'Kaputt', url: 'not a url' }
      ]
    })).toEqual([])
  })

  it('normalizes source URLs for inline citation matching', () => {
    expect(canonicalResearchSourceUrl('HTTPS://EXAMPLE.COM/report#section')).toBe(
      'https://example.com/report'
    )
    expect(canonicalResearchSourceUrl('https://user:secret@example.com/report')).toBeNull()
  })

  it('handles missing research source data', () => {
    expect(linkedResearchSources(null)).toEqual([])
    expect(linkedResearchSources({ linkedSources: null })).toEqual([])
  })
})
