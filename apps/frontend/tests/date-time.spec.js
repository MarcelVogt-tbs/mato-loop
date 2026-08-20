import { describe, expect, it } from 'vitest'

import {
  formatAbsoluteDateTime,
  formatDateTimeLocalInput,
  formatRelativeDateTime,
  localDateTimeToIso
} from '../src/utils/date-time'

describe('date-time formatting', () => {
  const now = new Date(2026, 6, 25, 16, 0)

  it('formats recent message times relatively', () => {
    expect(formatRelativeDateTime(new Date(2026, 6, 25, 15, 59, 30), now)).toBe('Gerade eben')
    expect(formatRelativeDateTime(new Date(2026, 6, 25, 15, 48), now)).toBe('Vor 12 Min.')
  })

  it('formats messages from today and yesterday with their time', () => {
    expect(formatRelativeDateTime(new Date(2026, 6, 25, 14, 32), now)).toBe('Heute, 14:32')
    expect(formatRelativeDateTime(new Date(2026, 6, 24, 14, 32), now)).toBe('Gestern, 14:32')
  })

  it('formats older and absolute message times with the date', () => {
    const value = new Date(2026, 6, 20, 14, 32)

    expect(formatRelativeDateTime(value, now)).toBe('20.07.2026, 14:32')
    expect(formatAbsoluteDateTime(value)).toBe('20.07.2026, 14:32')
  })

  it('omits missing or invalid values', () => {
    expect(formatRelativeDateTime(null, now)).toBe('')
    expect(formatRelativeDateTime('invalid', now)).toBe('')
    expect(formatAbsoluteDateTime(null)).toBe('')
  })

  it('converts browser-local date-time input to and from ISO', () => {
    const value = new Date(2026, 7, 21, 10, 30)

    expect(formatDateTimeLocalInput(value)).toBe('2026-08-21T10:30')
    expect(localDateTimeToIso('2026-08-21T10:30')).toBe(value.toISOString())
  })
})
