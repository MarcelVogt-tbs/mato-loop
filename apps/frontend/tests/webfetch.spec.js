import { describe, expect, it } from 'vitest'

import {
  cleanUrlForDisplay
} from '../src/utils/webfetch'

describe('webfetch display', () => {
  it('removes credentials and query values from displayed URLs', () => {
    expect(cleanUrlForDisplay('https://user:secret@example.com/a?token=secret#part'))
      .toBe('https://example.com/a?…')
  })
})
