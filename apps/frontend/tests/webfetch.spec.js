import { describe, expect, it } from 'vitest'

import {
  cleanUrlForDisplay,
  normalizePrivateOrigin,
  resolveWebfetchPolicy
} from '../src/utils/webfetch'

describe('webfetch display and settings', () => {
  it('resolves model overrides against the global hard gate', () => {
    expect(resolveWebfetchPolicy('inherit', { enabled: true, defaultPolicy: 'ask' })).toBe('ask')
    expect(resolveWebfetchPolicy('auto_public', { enabled: true, defaultPolicy: 'ask' })).toBe('auto_public')
    expect(resolveWebfetchPolicy('auto_allowed', { enabled: false, defaultPolicy: 'ask' })).toBe('disabled')
  })

  it('normalizes valid origins and rejects complete URLs or credentials', () => {
    expect(normalizePrivateOrigin(' HTTPS://Example.COM:443/ ')).toEqual({
      valid: true,
      value: 'https://example.com',
      message: ''
    })
    expect(normalizePrivateOrigin('https://example.com/private').valid).toBe(false)
    expect(normalizePrivateOrigin('https://user:secret@example.com').valid).toBe(false)
  })

  it('removes credentials and query values from displayed URLs', () => {
    expect(cleanUrlForDisplay('https://user:secret@example.com/a?token=secret#part'))
      .toBe('https://example.com/a?…')
  })
})
