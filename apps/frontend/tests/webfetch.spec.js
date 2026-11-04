import { describe, expect, it } from 'vitest'

import {
  cleanUrlForDisplay
} from '../src/shared/url-display'

describe('webfetch display', () => {
  it('removes credentials while preserving query values', () => {
    expect(cleanUrlForDisplay('https://user:secret@example.com/a?token=secret#part'))
      .toBe('https://example.com/a?token=secret')
  })
})
