import { formatAbsoluteDateTime } from 'src/utils/date-time'

export const schedulePresets = [
  { label: 'Täglich', value: 'daily' },
  { label: 'Werktags (Mo-Fr)', value: 'weekdays' },
  { label: 'Wöchentlich', value: 'weekly' },
  { label: 'Monatlich', value: 'monthly' },
  { label: 'Eigener Rhythmus', value: 'custom' }
]

export const weekdays = ['Sonntag', 'Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag']
  .map((label, value) => ({ label, value }))

export function browserTimezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

export function cronPreset(cron) {
  const fallback = { preset: 'custom', time: '08:00', weekday: 1, monthDay: 1 }
  const match = /^(0|[1-5]?\d) (0|1?\d|2[0-3]) (\*|[1-9]|[12]\d|3[01]) \* (\*|1-5|[0-6])$/.exec(cron || '')
  if (!match) return fallback
  const [, minute, hour, day, weekday] = match
  const preset = day === '*'
    ? weekday === '*' ? 'daily' : weekday === '1-5' ? 'weekdays' : 'weekly'
    : weekday === '*' ? 'monthly' : 'custom'
  if (preset === 'custom') return fallback
  return {
    preset, time: `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`,
    weekday: preset === 'weekly' ? Number(weekday) : 1,
    monthDay: preset === 'monthly' ? Number(day) : 1
  }
}

export function applySchedulePreset(schedule) {
  if (schedule.preset === 'custom') return
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(schedule.time)) return
  const [hour, minute] = schedule.time.split(':').map(Number)
  const day = schedule.preset === 'monthly' ? schedule.monthDay : '*'
  const weekday = schedule.preset === 'weekdays' ? '1-5' : schedule.preset === 'weekly' ? schedule.weekday : '*'
  schedule.cron = `${minute} ${hour} ${day} * ${weekday}`
}

export function scheduleDateInput(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, -1)
}

export function scheduleDraft(schedule = {}) {
  return {
    kind: schedule.runAt ? 'once' : 'cron',
    cron: schedule.cron ?? '0 8 * * *',
    ...cronPreset(schedule.cron ?? '0 8 * * *'),
    timezone: schedule.timezone ?? browserTimezone(),
    runAt: scheduleDateInput(schedule.runAt),
    validUntil: scheduleDateInput(schedule.validUntil),
    enabled: schedule.enabled ?? true,
    originalRunAt: schedule.runAt ?? null,
    originalValidUntil: schedule.validUntil ?? null,
    advanced: false
  }
}

function datePayload(input, original) {
  // Preserve offset, sub-millisecond precision and ambiguous DST times until edited.
  if (input === scheduleDateInput(original)) return original
  const date = new Date(input)
  return input && !Number.isNaN(date.getTime()) ? date.toISOString() : null
}

export function schedulePayload(schedule) {
  return schedule.kind === 'once'
    ? { runAt: datePayload(schedule.runAt, schedule.originalRunAt), enabled: schedule.enabled }
    : {
        cron: schedule.cron, timezone: schedule.timezone,
        validUntil: datePayload(schedule.validUntil, schedule.originalValidUntil), enabled: schedule.enabled
      }
}

export function scheduleDateValid(value) {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,3})?)?$/.test(value || '')) return false
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return false
  // Reject impossible dates and local times skipped by daylight saving changes.
  return scheduleDateInput(date).slice(0, 16) === value.slice(0, 16)
}

export function scheduleError(schedule) {
  if (schedule.kind === 'once') return scheduleDateValid(schedule.runAt) ? '' : 'Bitte einen gültigen Ausführungszeitpunkt angeben.'
  if (schedule.preset !== 'custom') {
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(schedule.time)) return 'Bitte eine gültige Uhrzeit angeben.'
    if (schedule.preset === 'monthly' && (!Number.isInteger(Number(schedule.monthDay)) || schedule.monthDay < 1 || schedule.monthDay > 31)) return 'Der Monatstag muss zwischen 1 und 31 liegen.'
  }
  if (String(schedule.cron || '').trim().split(/\s+/).length !== 5) return 'Die Zeitregel muss fünf durch Leerzeichen getrennte Angaben enthalten.'
  try {
    if (!schedule.timezone) return 'Bitte eine Zeitzone angeben.'
    Intl.DateTimeFormat('de-DE', { timeZone: schedule.timezone })
  } catch {
    return 'Bitte eine gültige Zeitzone angeben, zum Beispiel Europe/Berlin.'
  }
  if (schedule.validUntil && !scheduleDateValid(schedule.validUntil)) return 'Bitte ein gültiges Enddatum angeben.'
  return ''
}

export function scheduleSummary(schedule) {
  if (schedule.kind === 'once') return schedule.runAt ? `Einmalig · ${formatAbsoluteDateTime(schedule.runAt)}` : 'Einmalig · Zeitpunkt fehlt'
  if (schedule.preset === 'weekly') return `Jeden ${weekdays[schedule.weekday]?.label} um ${schedule.time} Uhr`
  if (schedule.preset === 'monthly') return `Am ${schedule.monthDay}. jedes Monats um ${schedule.time} Uhr`
  if (schedule.preset !== 'custom') return `${schedule.preset === 'weekdays' ? 'Montag bis Freitag' : 'Täglich'} um ${schedule.time} Uhr`
  const expression = String(schedule.cron || '').trim()
  const minutes = /^\*\/(\d+) \* \* \* \*$/.exec(expression)
  if (minutes && Number(minutes[1]) > 0 && 60 % Number(minutes[1]) === 0) return `Alle ${Number(minutes[1])} Minuten`
  if (expression === '* * * * *') return 'Jede Minute'
  const hourly = /^(\d{1,2}) \* \* \* \*$/.exec(expression)
  if (hourly && Number(hourly[1]) < 60) return Number(hourly[1]) === 0 ? 'Jede volle Stunde' : `Stündlich, ${Number(hourly[1])} Minuten nach der vollen Stunde`
  const yearly = /^(\d{1,2}) (\d{1,2}) (\d{1,2}) (\d{1,2}) \*$/.exec(expression)
  if (yearly) {
    const [, minute, hour, day, month] = yearly.map(Number)
    const date = new Date(Date.UTC(2000, month - 1, day))
    if (minute < 60 && hour < 24 && month >= 1 && month <= 12 && day >= 1 && date.getUTCDate() === day) {
      const dateLabel = new Intl.DateTimeFormat('de-DE', { day: 'numeric', month: 'long', timeZone: 'UTC' }).format(date)
      return `Jährlich am ${dateLabel} um ${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')} Uhr`
    }
  }
  return 'Individueller Zeitplan'
}
