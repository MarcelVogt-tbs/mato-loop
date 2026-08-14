import { technicalDetailsDisplay } from 'src/utils/approval-display'
import { apiErrorMessage, errorDescriptor } from 'src/utils/api-errors'
import { cleanUrlForDisplay } from 'src/utils/webfetch'

const TOOL_DISPLAY = {
  question: { label: 'Rückfrage stellen', icon: 'help_outline' },
  read: { label: 'Datei lesen', icon: 'description' },
  glob: { label: 'Dateien suchen', icon: 'folder_open' },
  grep: { label: 'Dateiinhalte durchsuchen', icon: 'manage_search' },
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
  generate_qr_code: { label: 'QR-Code erzeugen', icon: 'qr_code_2' },
  read_qr_code: { label: 'QR-Code lesen', icon: 'qr_code_scanner' },
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
  tasks_list_accounts: { label: 'Aufgabenkonten auflisten', icon: 'account_circle' },
  tasks_list_lists: { label: 'Aufgabenlisten auflisten', icon: 'checklist' },
  tasks_query: { label: 'Aufgaben lesen', icon: 'task_alt' },
  tasks_create: { label: 'Aufgabe erstellen', icon: 'add_task' },
  tasks_update: { label: 'Aufgabe ändern', icon: 'edit_note' },
  tasks_set_completed: { label: 'Aufgabenstatus ändern', icon: 'task_alt' },
  tasks_delete: { label: 'Aufgabe löschen', icon: 'delete_outline' },
  session_read: { label: 'Session lesen', icon: 'forum' },
  session_tool_result_read: { label: 'Tool-Ergebnis lesen', icon: 'history' },
  session_browse: { label: 'Letzte Sessions anzeigen', icon: 'view_list' },
  session_search: { label: 'Sessions durchsuchen', icon: 'manage_search' },
  memory_retrieve: { label: 'Memory abrufen', icon: 'psychology' },
  memory_write: { label: 'Memory speichern', icon: 'save' },
  update_agent_identity: { label: 'Persona ändern', icon: 'face' },
  process_exec: { label: 'Prozess ausführen', icon: 'terminal' },
  'process.exec': { label: 'Prozess ausführen', icon: 'terminal' },
  shell_run: { label: 'Shell-Befehl ausführen', icon: 'terminal' },
  'shell.run': { label: 'Shell-Befehl ausführen', icon: 'terminal' },
  git_clone: { label: 'Git-Repository klonen', icon: 'account_tree' },
  git_status: { label: 'Git-Status anzeigen', icon: 'fact_check' },
  git_diff: { label: 'Git-Änderungen anzeigen', icon: 'difference' },
  git_log: { label: 'Git-Verlauf anzeigen', icon: 'history' },
  git_add: { label: 'Git-Änderungen vormerken', icon: 'add_task' },
  git_commit: { label: 'Git-Commit erstellen', icon: 'commit' }
}

function truncate(value, maxLength = 140) {
  const text = String(value || '').trim().replace(/\s+/g, ' ')
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text
}

export function toolIdentity(name) {
  return TOOL_DISPLAY[name] || { label: 'Tool ausführen', icon: 'build' }
}

export function toolUsageSummary(items = []) {
  const tools = new Map()
  for (const item of items) {
    const name = item?.tool?.name
    if (!name) continue
    const existing = tools.get(name)
    if (existing) {
      existing.count += 1
      existing.active ||= Boolean(item.active)
    } else {
      tools.set(name, { name, ...toolIdentity(name), count: 1, active: Boolean(item.active) })
    }
  }
  return [...tools.values()]
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
  const fallback = TOOL_DISPLAY[tool?.name] ? '' : tool?.name || ''
  if (!argumentsValue || typeof argumentsValue !== 'object') return fallback
  if (tool?.name === 'memory_retrieve' && argumentsValue.mode === 'browse') {
    return argumentsValue.cursor_id ? 'Memory-Übersicht fortsetzen' : 'Memory-Übersicht'
  }
  if (tool?.name === 'session_browse') return 'Letzte Sessions'
  if (tool?.name === 'git_clone') {
    const url = argumentsValue.repository_url || argumentsValue.url
    const destination = argumentsValue.destination || argumentsValue.target_path || argumentsValue.path
    return truncate([url ? cleanUrlForDisplay(url) : '', destination].filter(Boolean).join(' -> '))
  }
  if (tool?.name === 'git_add' && Array.isArray(argumentsValue.paths)) {
    return truncate(argumentsValue.paths.join(', '))
  }
  if (tool?.name === 'git_commit' && typeof argumentsValue.message === 'string') {
    return truncate(argumentsValue.message)
  }
  if (['git_status', 'git_diff', 'git_log'].includes(tool?.name)) {
    return truncate(
      argumentsValue.repository_path || argumentsValue.repositoryPath || argumentsValue.path || ''
    )
  }
  if (tool?.name === 'generate_qr_code') return 'QR-Code'
  if (tool?.name === 'read_qr_code' && typeof argumentsValue.attachment_id === 'string') {
    return `Bildanhang ${truncate(argumentsValue.attachment_id, 36)}`
  }
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
  if (['read', 'glob', 'grep'].includes(tool?.name)) {
    const location = [argumentsValue.root, argumentsValue.path]
      .filter((value) => typeof value === 'string' && value)
      .join(':')
    const pattern = typeof argumentsValue.pattern === 'string' ? argumentsValue.pattern : ''
    return truncate([location, pattern].filter(Boolean).join(' · '))
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
  return fallback
}

export function toolArgumentsDisplay(argumentsValue, toolName = null) {
  if (toolName === 'memory_write' && argumentsValue && typeof argumentsValue === 'object') {
    const metadata = { ...argumentsValue }
    delete metadata.content
    return technicalDetailsDisplay(metadata)
  }
  return technicalDetailsDisplay(argumentsValue)
}

export function formatToolArguments(argumentsValue, toolName = null) {
  return toolArgumentsDisplay(argumentsValue, toolName).text
}

function localizePlainToolResult(content, toolName) {
  const mediaResults = {
    generate_qr_code: ['QR code generated.', 'QR-Code erzeugt.'],
    generate_image: ['Image generated.', 'Bild erzeugt.'],
    generate_speech: ['Speech generated.', 'Sprachaufnahme erzeugt.'],
    generate_audio: ['Audio generated.', 'Audio erzeugt.']
  }
  const mediaResult = mediaResults[toolName]
  let text = mediaResult && content.startsWith(mediaResult[0])
    ? `${mediaResult[1]}${content.slice(mediaResult[0].length)}`
    : content
  if (['read', 'read_file'].includes(toolName)) {
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
    const processValue = value?.output && typeof value.output === 'object' ? value.output : value
    const hasProcessOutput = typeof processValue?.stdout === 'string'
      || typeof processValue?.stderr === 'string'
    const processMetadata = hasProcessOutput ? { ...value } : null
    if (processMetadata) {
      if (processMetadata.output === processValue) delete processMetadata.output
      else {
        delete processMetadata.stdout
        delete processMetadata.stderr
        delete processMetadata.truncated
      }
    }
    const processMetadataDisplay = processMetadata && Object.keys(processMetadata).length
      ? technicalDetailsDisplay(processMetadata)
      : null
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
            memory: item.content
          }))
        }
      : null
    const display = memoryValue
      ? technicalDetailsDisplay(memoryValue)
      : processMetadata
        ? processMetadataDisplay || { text: '', fullText: '', truncated: false }
        : technicalDetailsDisplay(value)
    return {
      text: display.text,
      fullText: display.fullText,
      textTruncated: display.truncated,
      error,
      errorMessage: error ? apiErrorMessage(error, 'Die Tool-Aktion ist fehlgeschlagen.') : null,
      ...(hasProcessOutput
        ? {
            stdout: processValue.stdout || '',
            stderr: processValue.stderr || '',
            truncated: processValue.truncated === true
          }
        : {})
    }
  } catch {
    const localized = localizePlainToolResult(content, toolName)
    const text = localized.length > 5000 ? `${localized.slice(0, 5000)}\n... [Ergebnis gekürzt]` : localized
    return {
      text,
      fullText: localized,
      textTruncated: text !== localized,
      error: null,
      errorMessage: null
    }
  }
}

export function toolStatus({ result, approval, question, active }) {
  if (approval) {
    return { name: 'approval', label: 'Freigabe erforderlich', icon: 'pending_actions', color: 'warning' }
  }
  if (question) {
    return { name: 'question', label: 'Antwort erforderlich', icon: 'help_outline', color: 'primary' }
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
