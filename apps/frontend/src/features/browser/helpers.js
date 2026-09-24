import { apiErrorMessage } from 'src/utils/api-errors'

export const MAX_CLIPBOARD_BYTES = 16 * 1024

export function browserSocketUrl(integrationId, suffix = '', params = {}, location = globalThis.location) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const query = new URLSearchParams(params).toString()
  return `${protocol}//${location.host}/api/browsers/${encodeURIComponent(integrationId)}${suffix}${query ? `?${query}` : ''}`
}

export function browserStatusLabel(status) {
  if (status?.errorCode) return apiErrorMessage({ error: { code: status.errorCode } })
  return ({
    missing: 'Browser muss eingerichtet werden.',
    installing: 'Browser wird eingerichtet. Die Einrichtung läuft auch ohne geöffneten Dialog weiter.',
    ready: 'Browser ist einsatzbereit.',
    error: 'Browser ist nicht einsatzbereit. Bitte Einrichtung und Status prüfen.',
    unsupported: 'Dieser Computer unterstützt den Browser nicht.'
  })[status?.state] || 'Browserstatus noch nicht geprüft.'
}

export function clipboardFits(text) {
  return typeof text === 'string' && new TextEncoder().encode(text).length < MAX_CLIPBOARD_BYTES
}

export function browserNavigationUrl(input) {
  const text = input.trim()
  if (text === 'about:blank') return text
  if (!text || /[\s\\]/u.test(text)) return null
  const hasScheme = /^[a-z][a-z\d+.-]*:/i.test(text) && !/^[^/:]+:\d+(?:[/?#]|$)/.test(text)
  if (hasScheme && !/^https?:\/\//i.test(text)) return null
  try {
    const url = new URL(hasScheme ? text : `https://${text}`)
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) return null
    if (!hasScheme && (url.hostname === 'localhost' || url.hostname.endsWith('.localhost')
      || url.hostname === '[::1]' || /^127(?:\.\d{1,3}){3}$/.test(url.hostname))) url.protocol = 'http:'
    return url.href.length <= 4096 ? url.href : null
  } catch { return null }
}
