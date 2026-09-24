export function cleanUrlForDisplay(value, maxLength = 140) {
  try {
    const url = new URL(String(value || ''))
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname) return 'Ungültige URL'
    const path = url.pathname === '/' ? '' : url.pathname
    const display = `${url.protocol}//${url.host}${path}${url.search}`
    return display.length > maxLength ? `${display.slice(0, maxLength)}...` : display
  } catch {
    return 'Ungültige URL'
  }
}
