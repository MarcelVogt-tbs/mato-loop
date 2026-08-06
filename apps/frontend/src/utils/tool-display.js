import { formatTechnicalDetails } from 'src/utils/approval-display'
import { apiErrorMessage, errorDescriptor } from 'src/utils/api-errors'
import { cleanUrlForDisplay } from 'src/utils/webfetch'

const TOOL_DISPLAY = {
  list_directory: { label: 'Ordner auflisten', icon: 'folder_open' },
  read_file: { label: 'Datei lesen', icon: 'description' },
  provide_file: { label: 'Datei bereitstellen', icon: 'download' },
  write_file: { label: 'Datei schreiben', icon: 'edit_document' },
  create_directory: { label: 'Ordner erstellen', icon: 'create_new_folder' },
  delete_path: { label: 'Pfad löschen', icon: 'delete_outline' },
  copy_path: { label: 'Pfad kopieren', icon: 'content_copy' },
  move_path: { label: 'Pfad verschieben', icon: 'drive_file_move' },
  web_search: { label: 'Im Web suchen', icon: 'travel_explore' },
  webfetch: { label: 'Webseite abrufen', icon: 'language' },
  generate_image: { label: 'Bild erzeugen', icon: 'image' },
  edit_image: { label: 'Bild bearbeiten', icon: 'auto_fix_high' },
  generate_speech: { label: 'Sprache erzeugen', icon: 'record_voice_over' },
  generate_audio: { label: 'Audio erzeugen', icon: 'graphic_eq' },
  transcribe_audio: { label: 'Audio transkribieren', icon: 'mic' },
  analyze_video: { label: 'Video analysieren', icon: 'video_file' },
  calendar_list_accounts: { label: 'Kalenderkonten auflisten', icon: 'account_circle' },
  calendar_list_calendars: { label: 'Kalender auflisten', icon: 'calendar_month' },
  calendar_query_events: { label: 'Termine lesen', icon: 'event' },
  calendar_create_event: { label: 'Termin erstellen', icon: 'event_available' },
  calendar_update_event: { label: 'Termin ändern', icon: 'edit_calendar' },
  calendar_delete_event: { label: 'Termin löschen', icon: 'event_busy' },
  session_read: { label: 'Session lesen', icon: 'forum' },
  session_tool_result_read: { label: 'Tool-Ergebnis lesen', icon: 'history' },
  session_browse: { label: 'Letzte Sessions anzeigen', icon: 'view_list' },
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

export function memoryWriteContent(tool) {
  if (tool?.name !== 'memory_write') return ''
  const content = tool?.arguments?.content
  if (typeof content === 'string') return content
  if (typeof content?.preview !== 'string') return ''
  return Number(content.characters) > content.preview.length
    ? `${content.preview}\n... [älterer Verlauf gekürzt]`
    : content.preview
}

export function toolSummary(tool) {
  const argumentsValue = tool?.arguments
  if (!argumentsValue || typeof argumentsValue !== 'object') return tool?.name || ''
  if (tool?.name === 'memory_retrieve' && argumentsValue.mode === 'browse') {
    return argumentsValue.cursor_id ? 'Memory-Übersicht fortsetzen' : 'Memory-Übersicht'
  }
  if (tool?.name === 'session_browse') return 'Letzte Sessions'
  if (tool?.name === 'generate_speech' && typeof argumentsValue.text === 'string') {
    return truncate(argumentsValue.text)
  }
  if (['generate_image', 'edit_image', 'generate_audio'].includes(tool?.name) && typeof argumentsValue.prompt === 'string') {
    const duration = Number(argumentsValue.duration_seconds) > 0
      ? ` (${argumentsValue.duration_seconds} s)`
      : ''
    return `${truncate(argumentsValue.prompt)}${duration}`
  }
  if (tool?.name === 'transcribe_audio' && typeof argumentsValue.attachment_id === 'string') {
    return `Audioanhang ${truncate(argumentsValue.attachment_id, 36)}`
  }
  if (tool?.name === 'analyze_video' && typeof argumentsValue.path === 'string') {
    const root = argumentsValue.root ? `${argumentsValue.root}:` : ''
    return truncate(`${root}${argumentsValue.path}`)
  }
  if (tool?.name === 'session_tool_result_read' && typeof argumentsValue.message_id === 'string') {
    const offset = Number(argumentsValue.offset) || 0
    const suffix = offset > 0 ? `, ab Zeichen ${offset}` : ''
    return `Nachricht ${truncate(argumentsValue.message_id, 36)}${suffix}`
  }
  if (tool?.name === 'memory_write') return truncate(memoryWriteContent(tool))
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

export function formatToolArguments(argumentsValue, toolName = null) {
  if (toolName === 'memory_write' && argumentsValue && typeof argumentsValue === 'object') {
    const metadata = { ...argumentsValue }
    delete metadata.content
    return formatTechnicalDetails(metadata)
  }
  return formatTechnicalDetails(argumentsValue)
}

function localizePlainToolResult(content, toolName) {
  const mediaResults = {
    generate_image: ['Image generated.', 'Bild erzeugt.'],
    generate_speech: ['Speech generated.', 'Sprachaufnahme erzeugt.'],
    generate_audio: ['Audio generated.', 'Audio erzeugt.']
  }
  const mediaResult = mediaResults[toolName]
  let text = mediaResult && content.startsWith(mediaResult[0])
    ? `${mediaResult[1]}${content.slice(mediaResult[0].length)}`
    : content
  if (toolName === 'read_file') {
    text = text
      .replace(/\n\n\[Lines ([\d, ]+) were truncated after 2000 characters\.\](?=\n|$)/,
        '\n\n[Zeilen $1 wurden nach 2000 Zeichen gekürzt.]')
      .replace(/\n\[More lines are available\. Continue with offset=(\d+)\.\]$/,
        '\n[Weitere Zeilen verfügbar. Mit offset=$1 weiterlesen.]')
  }
  const imageMarker = '\n\n[Image saved but not sent to the current text-only model.]'
  return ['generate_image', 'webfetch'].includes(toolName) && text.endsWith(imageMarker)
    ? `${text.slice(0, -imageMarker.length)}\n\n[Bild gespeichert, aber nicht an das aktuelle Textmodell übertragen.]`
    : text
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
            pinned: item.pinned,
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
    const localized = localizePlainToolResult(content, toolName)
    const text = localized.length > 5000 ? `${localized.slice(0, 5000)}\n... [Ergebnis gekürzt]` : localized
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
