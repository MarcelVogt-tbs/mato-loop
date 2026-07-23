export const WEBFETCH_POLICY_OPTIONS = Object.freeze([
  { value: 'inherit', label: 'Globalen Standard übernehmen' },
  { value: 'disabled', label: 'Deaktiviert' },
  { value: 'ask', label: 'Immer nachfragen' },
  { value: 'auto_public', label: 'Öffentliche Ziele automatisch' },
  { value: 'auto_allowed', label: 'Öffentliche und erlaubte private Ziele automatisch' }
])

export const WEBFETCH_DEFAULT_POLICY_OPTIONS = WEBFETCH_POLICY_OPTIONS.filter(
  (option) => option.value !== 'inherit'
)

export function webfetchPolicyLabel(policy) {
  return WEBFETCH_POLICY_OPTIONS.find((option) => option.value === policy)?.label || policy || 'Unbekannt'
}

export function resolveWebfetchPolicy(override, settings, fallback = null) {
  if (settings?.enabled === false) return 'disabled'
  if (override && override !== 'inherit') return override
  return settings?.defaultPolicy || fallback
}

export function normalizePrivateOrigin(value) {
  const input = String(value || '').trim()
  if (!input) return { valid: false, value: '', message: 'Bitte gib eine Origin ein.' }
  try {
    const url = new URL(input)
    if (!['http:', 'https:'].includes(url.protocol)
      || url.username
      || url.password
      || !url.hostname
      || !['', '/'].includes(url.pathname)
      || url.search
      || url.hash) {
      throw new Error('invalid origin')
    }
    return { valid: true, value: url.origin, message: '' }
  } catch {
    return {
      valid: false,
      value: input,
      message: 'Nur HTTP(S)-Origins ohne Pfad, Zugangsdaten, Query oder Fragment sind erlaubt.'
    }
  }
}

export function cleanUrlForDisplay(value, maxLength = 140) {
  try {
    const url = new URL(String(value || ''))
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) return 'Ungültige URL'
    const path = url.pathname === '/' ? '' : url.pathname
    const queryMarker = url.search ? '?…' : ''
    const display = `${url.protocol}//${url.host}${path}${queryMarker}`
    return display.length > maxLength ? `${display.slice(0, maxLength)}...` : display
  } catch {
    return 'Ungültige URL'
  }
}
