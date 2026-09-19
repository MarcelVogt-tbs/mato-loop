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

export function memoryApprovalDisplay(approval) {
  const payload = approval?.payload && typeof approval.payload === 'object' ? approval.payload : {}
  const targets = Array.isArray(payload.targetMemories)
    ? payload.targetMemories.filter((item) => item && typeof item === 'object')
    : []
  const action = {
    memory_update: {
      title: 'Erinnerung ändern',
      summary: 'Dreaming möchte eine bestehende Erinnerung aktualisieren.',
      targetLabel: 'Bisherige Erinnerung',
      icon: 'psychology'
    },
    memory_merge: {
      title: 'Erinnerungen zusammenführen',
      summary: 'Dreaming möchte mehrere Erinnerungen zu einer Erinnerung zusammenführen.',
      targetLabel: 'Bisherige Erinnerungen',
      icon: 'psychology'
    },
    memory_remove: {
      title: 'Erinnerung dauerhaft entfernen',
      summary: 'Dreaming möchte diese Erinnerung unwiderruflich löschen.',
      targetLabel: 'Zu entfernende Erinnerung',
      icon: 'delete_forever',
      destructive: true
    },
    memory_pin: {
      title: payload.requestedPin ? 'Erinnerung: Immer im Kontext' : 'Erinnerung: Bei passender Anfrage',
      summary: 'Dreaming möchte die Berücksichtigung einer Erinnerung ändern.',
      targetLabel: 'Bisherige Erinnerung',
      icon: 'push_pin'
    }
  }[approval?.actionKind] || {
    title: 'Memory-Änderung prüfen',
    summary: 'Dreaming möchte eine Erinnerung ändern.',
    targetLabel: 'Bisherige Erinnerung',
    icon: 'psychology'
  }
  const afterRequired = !['memory_remove', 'memory_pin'].includes(approval?.actionKind)
  return {
    ...action,
    payload,
    targets,
    invalid: !targets.length
      || !Array.isArray(approval?.availableDecisions)
      || (afterRequired && (!payload.after || typeof payload.after !== 'object'))
  }
}

export function approvalResource(approval, sessions = []) {
  const scope = String(approval?.resourceScope || '')
  if (['path', 'root_path', 'execution_root_path'].includes(approval?.resourceType)) {
    return {
      label: 'Datei oder Ordner',
      value: approval?.resourceDisplay || scope || 'Unbekannter Pfad'
    }
  }
  if (approval?.resourceType === 'execution_directory') {
    return {
      label: 'Aktueller Ordner',
      value: approval?.resourceDisplay || 'Ausgewählter Ordner'
    }
  }
  if (approval?.resourceType === 'execution_agent') {
    return {
      label: 'Computer',
      value: approval?.resourceDisplay || 'Ausgewählter Computer'
    }
  }
  if (approval?.resourceType === 'execution_workspace') {
    return {
      label: 'Aktueller Ordner',
      value: approval?.resourceDisplay || 'Ausgewählter Computer'
    }
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
    const name = detailName(approval, ['resourceDisplay'])
      || detailName(approval.details, ['sessionName', 'sessionTitle', 'resourceName'])
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
  if (approval?.resourceType === 'integration_resource') {
    return {
      label: 'Integrationsbereich',
      value: approval?.resourceDisplay || 'Gebundener Integrationsbereich'
    }
  }
  if (approval?.resourceType === 'memory_collection') {
    return { label: 'Memory-Sammlung', value: approval?.resourceDisplay || 'Eigene Memories' }
  }
  if (approval?.resourceType === 'user_profile') {
    return { label: 'Persönliches Profil', value: approval?.resourceDisplay || 'Eigenes persönliches Profil' }
  }
  if (approval?.resourceType === 'personal_variable') {
    return { label: 'Persönliche Variable', value: approval?.resourceDisplay ? `„${approval.resourceDisplay}“` : 'Ausgewählte Variable' }
  }
  if (approval?.resourceType === 'personal_variable_collection') {
    return { label: 'Variablen & Secrets', value: 'Eigene Variablen und Secrets' }
  }
  if (approval?.resourceType === 'skill') {
    return { label: 'Skill', value: approval?.resourceDisplay ? `„${approval.resourceDisplay}“` : scope }
  }
  if (approval?.resourceType === 'skill_collection') {
    return {
      label: 'Skill-Sammlung',
      value: approval?.resourceDisplay ? `„${approval.resourceDisplay}“` : 'Persönliche Skills'
    }
  }
  if (approval?.resourceType === 'automation_collection') {
    return { label: 'Automationen', value: approval?.resourceDisplay || 'Eigene Automationen' }
  }
  if (approval?.resourceType === 'automation') {
    return { label: 'Automation', value: approval?.resourceDisplay || 'Ausgewählte Automation' }
  }
  return {
    label: 'Angeforderte Ressource',
    value: approval?.resourceDisplay || scope || 'Nicht angegeben'
  }
}

export function shellClassificationDisplay(approval) {
  const classification = approval?.payload?.shellClassification
  if (!['read', 'write'].includes(classification)) return null
  return {
    color: classification === 'read' ? 'primary' : 'warning',
    label: classification === 'read' ? 'Keine Schreibaktion erkannt' : 'Als schreibend eingestuft',
    incomplete: approval.payload.shellAnalysisComplete === false
  }
}

function sanitize(value, depth, seen, key = '', truncate = true) {
  if (value == null || typeof value === 'boolean' || typeof value === 'number') return value
  if (typeof value === 'string') {
    if (URL_KEY.test(key)) return cleanUrlForDisplay(value, truncate ? MAX_STRING_LENGTH : Infinity)
    return truncate && value.length > MAX_STRING_LENGTH
      ? `${value.slice(0, MAX_STRING_LENGTH)}... [gekürzt]`
      : value
  }
  if (typeof value !== 'object') return String(value)
  if (seen.has(value)) return '[Zirkuläre Referenz]'
  if (truncate && depth >= MAX_DEPTH) return '[Weitere Ebenen gekürzt]'
  seen.add(value)
  if (Array.isArray(value)) {
    const items = (truncate ? value.slice(0, MAX_ARRAY_ITEMS) : value)
      .map((item) => sanitize(item, depth + 1, seen, '', truncate))
    if (truncate && value.length > MAX_ARRAY_ITEMS) items.push(`[${value.length - MAX_ARRAY_ITEMS} weitere Einträge]`)
    return items
  }
  const result = {}
  const entries = Object.entries(value)
  for (const [key, item] of truncate ? entries.slice(0, MAX_OBJECT_KEYS) : entries) {
    result[key] = REDACTED_KEY.test(key) ? '[Ausgeblendet]' : sanitize(item, depth + 1, seen, key, truncate)
  }
  if (truncate && entries.length > MAX_OBJECT_KEYS) result._gekürzt = `${entries.length - MAX_OBJECT_KEYS} weitere Felder`
  return result
}

export function permissionGrantDisplay(grant, sessions = []) {
  const resource = approvalResource(grant, sessions)
  if (['resource.read', 'resource.write'].includes(grant?.capability)) {
    const includesShell = grant.resourceType === 'execution_directory'
    const includesChildren = ['execution_directory', 'root_path'].includes(grant.resourceType)
    const write = grant.capability === 'resource.write'
    const target = includesShell
      ? 'Datei- und Shell-Aktionen'
      : grant.resourceType === 'root_path' ? 'Dateiaktionen' : 'Aktionen'
    return {
      label: `Alle passenden ${write ? 'schreibenden' : 'lesenden'} ${target}`,
      value: includesChildren
        ? `${resource.value} und Unterordner`
        : resource.value,
      icon: write ? 'edit' : 'visibility'
    }
  }
  if (grant?.resourceType === 'shell_command') {
    const prefixes = Array.isArray(grant.shellPrefixes)
      ? grant.shellPrefixes
        .filter((prefix) => Array.isArray(prefix) && prefix.every((token) => typeof token === 'string'))
        .map((prefix) => prefix.join(' '))
      : []
    return {
      label: grant.capability === 'shell.read'
        ? 'Shell-Befehle ohne erkannte Schreibaktionen'
        : 'Als schreibend eingestufte Shell-Befehle',
      value: [resource.value, ...prefixes].join(' · '),
      icon: 'terminal'
    }
  }
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
  if (grant?.resourceType === 'skill') {
    return { label: 'Skill bearbeiten', value: resource.value, icon: 'edit_note' }
  }
  const capability = {
    'filesystem.read': { label: 'Dateien lesen', icon: 'visibility' },
    'filesystem.write': { label: 'Dateien schreiben', icon: 'edit' },
    'filesystem.delete': { label: 'Dateien löschen', icon: 'delete_outline' },
    'execution.directory': { label: 'Aktuellen Ordner wechseln', icon: 'drive_file_move' },
    'system.info': { label: 'Systeminformationen lesen', icon: 'computer' },
    'process.execute': { label: 'Befehl ausführen', icon: 'terminal' },
    'network.search': { label: 'Websuche verwenden', icon: 'travel_explore' },
    'session.read': { label: 'Session lesen', icon: 'forum' },
    'session.search': { label: 'Sessions durchsuchen', icon: 'manage_search' },
    'memory.read': { label: 'Memories lesen', icon: 'psychology' },
    'memory.write': { label: 'Memory speichern', icon: 'save' },
    'settings.user_profile.update': { label: 'Persönliches Profil ändern', icon: 'manage_accounts' },
    'secrets.access': { label: 'Variablen und Secrets verwenden', icon: 'key' },
    'secrets.write': { label: 'Variable oder Secret speichern', icon: 'save' },
    'skills.write': { label: 'Skills erstellen', icon: 'auto_awesome' },
    'automation.read': { label: 'Automationen anzeigen', icon: 'schedule' },
    'automation.create': { label: 'Automation erstellen', icon: 'add_task' },
    'automation.update': { label: 'Automation ändern', icon: 'edit_calendar' },
    'automation.run': { label: 'Automation starten', icon: 'play_arrow' },
    'automation.delete': { label: 'Automation löschen', icon: 'delete_forever' },
    'calendar.read': { label: 'Kalender lesen', icon: 'event' },
    'calendar.write': { label: 'Kalender ändern', icon: 'edit_calendar' },
    'calendar.delete': { label: 'Kalendereintrag löschen', icon: 'event_busy' },
    'tasks.read': { label: 'Aufgaben lesen', icon: 'checklist' },
    'tasks.write': { label: 'Aufgaben ändern', icon: 'edit_note' },
    'tasks.delete': { label: 'Aufgaben löschen', icon: 'delete_outline' },
    'mail.read': { label: 'E-Mails lesen', icon: 'mail' },
    'mail.drafts': { label: 'E-Mail-Entwürfe verwalten', icon: 'drafts' },
    'mail.send': { label: 'E-Mails senden', icon: 'send' },
    'mail.delete': { label: 'E-Mails löschen', icon: 'delete_outline' },
    'knowledge.read': { label: 'Wissensdatenbank lesen', icon: 'menu_book' },
    'knowledge.write': { label: 'Wissensdatenbank ändern', icon: 'edit_note' },
    'knowledge.delete': { label: 'Wissenseintrag löschen', icon: 'delete_sweep' },
    'mealie.accounts.read': { label: 'Mealie-Konten auflisten', icon: 'restaurant' },
    'recipes.read': { label: 'Rezepte lesen', icon: 'menu_book' },
    'meal_plan.read': { label: 'Essensplan lesen', icon: 'calendar_month' },
    'meal_plan.write': { label: 'Essensplan ändern', icon: 'edit_calendar' },
    'meal_plan.delete': { label: 'Essensplan-Eintrag löschen', icon: 'event_busy' },
    'integration.private_data.release': { label: 'Private Integrationsdaten freigeben', icon: 'cloud_upload' }
  }[grant?.capability]
  return {
    label: capability?.label || 'Tool-Zugriff',
    value: resource.value,
    icon: capability?.icon || 'verified_user'
  }
}

export function technicalDetailsDisplay(details) {
  if (details == null) return { text: '', fullText: '', truncated: false }
  const formatted = JSON.stringify(sanitize(details, 0, new WeakSet()), null, 2)
  const text = formatted.length > MAX_DETAILS_LENGTH
    ? `${formatted.slice(0, MAX_DETAILS_LENGTH)}\n... [Details gekürzt]`
    : formatted
  const fullText = JSON.stringify(sanitize(details, 0, new WeakSet(), '', false), null, 2)
  return { text, fullText, truncated: text !== fullText }
}
