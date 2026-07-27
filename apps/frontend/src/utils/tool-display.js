import { formatTechnicalDetails } from 'src/utils/approval-display'
import { apiErrorMessage, errorDescriptor } from 'src/utils/api-errors'
import { cleanUrlForDisplay } from 'src/utils/webfetch'

const TOOL_DISPLAY = {
  list_directory: { label: 'Ordner auflisten', icon: 'folder_open' },
  read_file: { label: 'Datei lesen', icon: 'description' },
  write_file: { label: 'Datei schreiben', icon: 'edit_document' },
  create_directory: { label: 'Ordner erstellen', icon: 'create_new_folder' },
  delete_path: { label: 'Pfad löschen', icon: 'delete_outline' },
  copy_path: { label: 'Pfad kopieren', icon: 'content_copy' },
  move_path: { label: 'Pfad verschieben', icon: 'drive_file_move' },
  web_search: { label: 'Im Web suchen', icon: 'travel_explore' },
  webfetch: { label: 'Webseite abrufen', icon: 'language' },
  generate_image: { label: 'Bild erzeugen', icon: 'image' },
  generate_speech: { label: 'Sprache erzeugen', icon: 'record_voice_over' },
  generate_audio: { label: 'Audio erzeugen', icon: 'graphic_eq' },
  transcribe_audio: { label: 'Audio transkribieren', icon: 'mic' },
  session_read: { label: 'Session lesen', icon: 'forum' },
  session_tool_result_read: { label: 'Tool-Ergebnis lesen', icon: 'history' },
  session_search: { label: 'Sessions durchsuchen', icon: 'manage_search' },
  memory_retrieve: { label: 'Memory abrufen', icon: 'psychology' },
  memory_write: { label: 'Memory speichern', icon: 'save' },
  update_agent_identity: { label: 'Persona ändern', icon: 'face' }
}

function truncate(value, maxLength = 140) {
  const text = String(value || '').trim().replace(/\s+/g, ' ')
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text
}

export function toolIdentity(name) {
  return TOOL_DISPLAY[name] || { label: 'Tool ausführen', icon: 'build' }
}

export function toolSummary(tool) {
  const argumentsValue = tool?.arguments
  if (!argumentsValue || typeof argumentsValue !== 'object') return tool?.name || ''
  if (tool?.name === 'memory_retrieve' && argumentsValue.mode === 'browse') {
    return argumentsValue.cursor_id ? 'Memory-Übersicht fortsetzen' : 'Memory-Übersicht'
  }
  if (tool?.name === 'generate_speech' && typeof argumentsValue.text === 'string') {
    return truncate(argumentsValue.text)
  }
  if (['generate_image', 'generate_audio'].includes(tool?.name) && typeof argumentsValue.prompt === 'string') {
    const duration = Number(argumentsValue.duration_seconds) > 0
      ? ` (${argumentsValue.duration_seconds} s)`
      : ''
    return `${truncate(argumentsValue.prompt)}${duration}`
  }
  if (tool?.name === 'transcribe_audio' && typeof argumentsValue.attachment_id === 'string') {
    return `Audioanhang ${truncate(argumentsValue.attachment_id, 36)}`
  }
  if (tool?.name === 'session_tool_result_read' && typeof argumentsValue.message_id === 'string') {
    const offset = Number(argumentsValue.offset) || 0
    const suffix = offset > 0 ? `, ab Zeichen ${offset}` : ''
    return `Nachricht ${truncate(argumentsValue.message_id, 36)}${suffix}`
  }
  if (tool?.name === 'update_agent_identity') {
    if (typeof argumentsValue.display_name === 'string') {
      return `Agent-Name: ${truncate(argumentsValue.display_name, 80)}`
    }
    return 'Verhalten und Charakter aktualisieren'
  }
  if (typeof argumentsValue.path === 'string') return truncate(argumentsValue.path)
  if (typeof argumentsValue.source_path === 'string' && typeof argumentsValue.destination_path === 'string') {
    const sourceRoot = argumentsValue.source_root ? `${argumentsValue.source_root}:` : ''
    const destinationRoot = argumentsValue.destination_root ? `${argumentsValue.destination_root}:` : ''
    return truncate(`${sourceRoot}${argumentsValue.source_path} -> ${destinationRoot}${argumentsValue.destination_path}`)
  }
  if (typeof argumentsValue.url === 'string') return cleanUrlForDisplay(argumentsValue.url)
  if (typeof argumentsValue.query === 'string') return truncate(argumentsValue.query)
  if (typeof argumentsValue.name === 'string') return truncate(argumentsValue.name)
  if (typeof argumentsValue.sessionName === 'string') return truncate(argumentsValue.sessionName)
  return tool?.name || ''
}

export function formatToolArguments(argumentsValue) {
  return formatTechnicalDetails(argumentsValue)
}

export function parseToolResult(content, toolName = null) {
  if (typeof content !== 'string') return { text: '', error: null }
  try {
    const value = JSON.parse(content)
    const error = errorDescriptor(value)
    const memoryValue = toolName === 'memory_retrieve' && Array.isArray(value?.items)
      ? {
          mode: value.mode,
          strategy: value.strategy,
          scope: value.scope,
          total: value.total,
          hasMore: value.hasMore,
          nextCursor: value.nextCursor,
          items: value.items.slice(0, 20).map((item) => ({
            id: item.id,
            category: item.category,
            retrievalLevel: item.retrievalLevel,
            trustLevel: item.trustLevel,
            memory: truncate(item.content, 500)
          }))
        }
      : null
    return {
      text: memoryValue ? JSON.stringify(memoryValue, null, 2) : formatTechnicalDetails(value),
      error,
      errorMessage: error ? apiErrorMessage(error, 'Die Tool-Aktion ist fehlgeschlagen.') : null
    }
  } catch {
    const text = content.length > 5000 ? `${content.slice(0, 5000)}\n... [Ergebnis gekürzt]` : content
    return { text, error: null, errorMessage: null }
  }
}

export function toolStatus({ result, approval, active }) {
  if (approval) {
    return { name: 'approval', label: 'Freigabe erforderlich', icon: 'pending_actions', color: 'warning' }
  }
  if (result) {
    const { error } = parseToolResult(result.content)
    if (error?.code === 'permission.denied') {
      return { name: 'denied', label: 'Abgelehnt', icon: 'block', color: 'negative' }
    }
    if (error?.code === 'tool.executionInterrupted') {
      return { name: 'interrupted', label: 'Unterbrochen', icon: 'stop_circle', color: 'warning' }
    }
    if (error) return { name: 'failed', label: 'Fehlgeschlagen', icon: 'error', color: 'negative' }
    return { name: 'completed', label: 'Erledigt', icon: 'check_circle', color: 'positive' }
  }
  if (active) return { name: 'running', label: 'Wird ausgeführt', icon: null, color: 'primary' }
  return { name: 'pending', label: 'Ausstehend', icon: 'schedule', color: 'grey-7' }
}
