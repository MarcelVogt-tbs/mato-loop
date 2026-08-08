import { cleanUrlForDisplay } from 'src/utils/webfetch'

const REDACTED_KEY = /(authorization|body|content|credential|password|secret|token|api[_-]?key)/i
const URL_KEY = /(url|uri)$/i
const MAX_STRING_LENGTH = 300
const MAX_ARRAY_ITEMS = 20
const MAX_OBJECT_KEYS = 30
const MAX_DEPTH = 4
const MAX_DETAILS_LENGTH = 5000

function detailName(details, keys) {
  if (!details || typeof details !== 'object') return null
  for (const key of keys) {
    const value = details[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

export function approvalResource(approval, sessions = []) {
  const scope = String(approval?.resourceScope || '')
  if (approval?.resourceType === 'path') {
    return { label: 'Datei oder Ordner', value: scope || 'Unbekannter Pfad' }
  }
  if (approval?.resourceType === 'url') {
    const url = approval?.resourceDisplay
      || approval?.details?.arguments?.url
      || approval?.details?.url
      || (/^[0-9a-f]{64}$/i.test(scope) ? null : scope)
    return {
      label: 'Webadresse',
      value: url ? cleanUrlForDisplay(url) : 'Exakt freigegebene Webadresse'
    }
  }
  if (approval?.resourceType === 'session') {
    const name = detailName(approval.details, ['sessionName', 'sessionTitle', 'resourceName'])
    const sessionTitle = sessions.find((session) => session.id === scope)?.title
    const currentSession = scope && scope === approval.sessionId
    return {
      label: 'Session',
      value: name
        ? `„${name}“`
        : currentSession
          ? 'Aktuelle Session'
          : sessionTitle
            ? `„${sessionTitle}“`
            : 'Andere Session'
    }
  }
  if (approval?.resourceType === 'session_collection') {
    const name = detailName(approval.details, ['collectionName', 'resourceName'])
    const allSessions = !scope || ['*', 'all', 'sessions'].includes(scope.toLowerCase())
    return {
      label: 'Session-Sammlung',
      value: name ? `„${name}“` : allSessions ? 'Alle Sessions' : scope
    }
  }
  if (approval?.resourceType === 'network_service') {
    return { label: 'Netzwerkdienst', value: approval?.resourceDisplay || 'Konfigurierte Websuche' }
  }
  if (approval?.resourceType === 'integration_operation') {
    return {
      label: 'Integrationsressource',
      value: approval?.resourceDisplay || 'Exakt angeforderte Integrationsaktion'
    }
  }
  if (approval?.resourceType === 'memory_collection') {
    return { label: 'Memory-Sammlung', value: approval?.resourceDisplay || 'Eigene Memories' }
  }
  return { label: 'Angeforderter Scope', value: scope || 'Nicht angegeben' }
}

function sanitize(value, depth, seen, key = '') {
  if (value == null || typeof value === 'boolean' || typeof value === 'number') return value
  if (typeof value === 'string') {
    if (URL_KEY.test(key)) return cleanUrlForDisplay(value, MAX_STRING_LENGTH)
    return value.length > MAX_STRING_LENGTH
      ? `${value.slice(0, MAX_STRING_LENGTH)}... [gekürzt]`
      : value
  }
  if (typeof value !== 'object') return String(value)
  if (seen.has(value)) return '[Zirkuläre Referenz]'
  if (depth >= MAX_DEPTH) return '[Weitere Ebenen gekürzt]'
  seen.add(value)
  if (Array.isArray(value)) {
    const items = value.slice(0, MAX_ARRAY_ITEMS).map((item) => sanitize(item, depth + 1, seen))
    if (value.length > MAX_ARRAY_ITEMS) items.push(`[${value.length - MAX_ARRAY_ITEMS} weitere Einträge]`)
    return items
  }
  const result = {}
  const entries = Object.entries(value)
  for (const [key, item] of entries.slice(0, MAX_OBJECT_KEYS)) {
    result[key] = REDACTED_KEY.test(key) ? '[Ausgeblendet]' : sanitize(item, depth + 1, seen, key)
  }
  if (entries.length > MAX_OBJECT_KEYS) result._gekürzt = `${entries.length - MAX_OBJECT_KEYS} weitere Felder`
  return result
}

export function permissionGrantDisplay(grant, sessions = []) {
  const resource = approvalResource(grant, sessions)
  if (grant?.resourceType === 'url') {
    const fingerprint = /^[0-9a-f]{64}$/i.test(String(grant.resourceScope || ''))
      ? ` · ${grant.resourceScope.slice(0, 8)}`
      : ''
    return {
      label: 'Webseite abrufen · Exakte URL',
      value: `${resource.value}${fingerprint}`,
      icon: 'language'
    }
  }
  const capability = {
    'filesystem.read': { label: 'Dateien lesen', icon: 'visibility' },
    'filesystem.write': { label: 'Dateien schreiben', icon: 'edit' },
    'filesystem.delete': { label: 'Dateien löschen', icon: 'delete_outline' },
    'network.search': { label: 'Websuche verwenden', icon: 'travel_explore' },
    'session.read': { label: 'Session lesen', icon: 'forum' },
    'session.search': { label: 'Sessions durchsuchen', icon: 'manage_search' },
    'memory.read': { label: 'Memories lesen', icon: 'psychology' },
    'memory.write': { label: 'Memory speichern', icon: 'save' },
    'calendar.read': { label: 'Kalender lesen', icon: 'event' },
    'calendar.write': { label: 'Kalender ändern', icon: 'edit_calendar' },
    'calendar.delete': { label: 'Kalendereintrag löschen', icon: 'event_busy' },
    'knowledge.read': { label: 'Wissensdatenbank lesen', icon: 'menu_book' },
    'knowledge.write': { label: 'Wissensdatenbank ändern', icon: 'edit_note' },
    'knowledge.delete': { label: 'Wissenseintrag löschen', icon: 'delete_sweep' },
    'integration.private_data.release': { label: 'Private Integrationsdaten freigeben', icon: 'cloud_upload' }
  }[grant?.capability]
  return {
    label: capability?.label || 'Tool-Zugriff',
    value: resource.value,
    icon: capability?.icon || 'verified_user'
  }
}

export function formatTechnicalDetails(details) {
  if (details == null) return ''
  const formatted = JSON.stringify(sanitize(details, 0, new WeakSet()), null, 2)
  return formatted.length > MAX_DETAILS_LENGTH
    ? `${formatted.slice(0, MAX_DETAILS_LENGTH)}\n... [Details gekürzt]`
    : formatted
}
