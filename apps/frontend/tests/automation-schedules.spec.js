import { describe, expect, it } from 'vitest'
import { applySchedulePreset, browserTimezone, cronPreset, scheduleDraft, scheduleError, schedulePayload, scheduleSummary } from '../src/utils/automation-schedules'

describe('automation schedule presets', () => {
  it.each([
    ['0 18 26 8 *', 'Jährlich am 26. August um 18:00 Uhr'],
    ['*/30 * * * *', 'Alle 30 Minuten'],
    ['0 * * * *', 'Jede volle Stunde'],
    ['0 8 * * 1,3', 'Individueller Zeitplan'],
    ['*/7 * * * *', 'Individueller Zeitplan']
  ])('describes %s without exposing or misrepresenting the time rule', (cron, label) => {
    expect(scheduleSummary(scheduleDraft({ cron }))).toBe(label)
  })
  it.each([
    ['0 8 * * *', 'daily'], ['30 17 * * 1-5', 'weekdays'], ['15 9 * * 0', 'weekly'], ['0 12 31 * *', 'monthly']
  ])('recognizes and round-trips %s as %s', (cron, preset) => {
    const draft = scheduleDraft({ cron, timezone: 'UTC', enabled: false })
    expect(draft.preset).toBe(preset)
    applySchedulePreset(draft)
    expect(draft.cron).toBe(cron)
    expect(schedulePayload(draft)).toEqual({ cron, timezone: 'UTC', validUntil: null, enabled: false })
  })

  it.each(['*/5 * * * *', '0 8 * * MON-FRI', '0 8 1 * 1', '00 08 * * *', '0 8 * 1 *', '0 8 * * 7', '0 8 * * 1,2', ' 0 8 * * * ', '60 8 * * *', '0 24 * * *'])('keeps unknown cron %s custom and unchanged', (cron) => {
    expect(cronPreset(cron).preset).toBe('custom')
    expect(schedulePayload(scheduleDraft({ cron, timezone: 'Europe/Berlin' })).cron).toBe(cron)
  })

  it('updates a preset only on explicit editing and uses the browser timezone', () => {
    const draft = scheduleDraft()
    expect(draft.timezone).toBe(browserTimezone())
    Object.assign(draft, { preset: 'weekly', weekday: 3, time: '21:37' })
    applySchedulePreset(draft)
    expect(draft.cron).toBe('37 21 * * 3')
    Object.assign(draft, { preset: 'monthly', monthDay: 12 })
    applySchedulePreset(draft)
    expect(draft.cron).toBe('37 21 12 * *')
  })

  it('preserves full original timestamps, offsets and disabled flags until edited', () => {
    const once = { runAt: '2026-10-25T02:30:47.123456+02:00', enabled: false }
    expect(schedulePayload(scheduleDraft(once))).toEqual(once)
    const recurring = { cron: '17 3 * * *', timezone: 'Europe/Berlin', validUntil: '2026-10-25T02:30:59.987654+01:00', enabled: false }
    const draft = scheduleDraft(recurring)
    expect(schedulePayload(draft)).toEqual(recurring)
    draft.validUntil = '2027-03-12T16:47:23.456'
    expect(schedulePayload(draft).validUntil).toBe(new Date(draft.validUntil).toISOString())
    draft.validUntil = ''
    expect(schedulePayload(draft).validUntil).toBeNull()
  })

  it('validates advanced timezone and timestamps as well as ordinary preset fields', () => {
    const draft = scheduleDraft()
    expect(scheduleError(draft)).toBe('')
    draft.timezone = 'Mars/Olympus'
    expect(scheduleError(draft)).toContain('Zeitzone')
    draft.timezone = 'UTC'
    draft.validUntil = '2026-02-30T10:00'
    expect(scheduleError(draft)).toContain('Enddatum')
    draft.validUntil = ''
    draft.time = ''
    expect(scheduleError(draft)).toContain('Uhrzeit')
    draft.kind = 'once'
    draft.runAt = 'invalid'
    expect(scheduleError(draft)).toContain('Ausführungszeitpunkt')
  })
})
