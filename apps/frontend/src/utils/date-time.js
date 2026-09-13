const absoluteDateTimeFormatter = new Intl.DateTimeFormat('de-DE', {
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit'
})

const timeFormatter = new Intl.DateTimeFormat('de-DE', {
  hour: '2-digit',
  minute: '2-digit'
})

function parseDate(value) {
  if (value === null || value === undefined || value === '') return null
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

function isSameDay(left, right) {
  return left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate()
}

export function formatAbsoluteDateTime(value) {
  const date = parseDate(value)
  return date ? absoluteDateTimeFormatter.format(date) : ''
}

export function formatRelativeDateTime(value, nowValue = new Date()) {
  const date = parseDate(value)
  const now = parseDate(nowValue)
  if (!date || !now) return ''

  const elapsedMilliseconds = now.getTime() - date.getTime()
  if (elapsedMilliseconds >= 0 && elapsedMilliseconds < 60_000) return 'Gerade eben'
  if (elapsedMilliseconds >= 0 && elapsedMilliseconds < 60 * 60_000) {
    return `Vor ${Math.floor(elapsedMilliseconds / 60_000)} Min.`
  }
  if (isSameDay(date, now)) return `Heute, ${timeFormatter.format(date)}`

  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (isSameDay(date, yesterday)) return `Gestern, ${timeFormatter.format(date)}`

  return absoluteDateTimeFormatter.format(date)
}
