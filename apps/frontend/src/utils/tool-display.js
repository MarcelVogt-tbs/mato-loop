import { technicalDetailsDisplay } from 'src/utils/approval-display'
import { apiErrorMessage, errorDescriptor } from 'src/utils/api-errors'
import { cleanUrlForDisplay } from 'src/utils/webfetch'

const TOOL_DISPLAY = {
  question: { label: 'Rückfrage stellen', activity: 'Rückfrage wird vorbereitet', icon: 'help_outline' },
  read: { label: 'Datei lesen', activity: 'Datei wird gelesen', icon: 'description' },
  glob: { label: 'Dateien suchen', activity: 'Dateien werden gesucht', icon: 'folder_open' },
  grep: { label: 'Dateiinhalte durchsuchen', activity: 'Dateiinhalte werden durchsucht', icon: 'manage_search' },
  list_directory: { label: 'Ordner auflisten', activity: 'Ordner wird aufgelistet', icon: 'folder_open' },
  read_file: { label: 'Datei lesen', activity: 'Datei wird gelesen', icon: 'description' },
  provide_file: { label: 'Datei bereitstellen', activity: 'Datei wird bereitgestellt', icon: 'download' },
  write_file: { label: 'Datei schreiben', activity: 'Datei wird geschrieben', icon: 'edit_document' },
  create_directory: { label: 'Ordner erstellen', activity: 'Ordner wird erstellt', icon: 'create_new_folder' },
  delete_path: { label: 'Pfad löschen', activity: 'Pfad wird gelöscht', icon: 'delete_outline' },
  copy_path: { label: 'Pfad kopieren', activity: 'Pfad wird kopiert', icon: 'content_copy' },
  move_path: { label: 'Pfad verschieben', activity: 'Pfad wird verschoben', icon: 'drive_file_move' },
  web_search: { label: 'Im Web suchen', activity: 'Web wird durchsucht', icon: 'travel_explore' },
  webfetch: { label: 'Webseite abrufen', activity: 'Webseite wird abgerufen', icon: 'language' },
  generate_qr_code: { label: 'QR-Code erzeugen', activity: 'QR-Code wird erzeugt', icon: 'qr_code_2' },
  read_qr_code: { label: 'QR-Code lesen', activity: 'QR-Code wird gelesen', icon: 'qr_code_scanner' },
  generate_image: { label: 'Bild erzeugen', activity: 'Bild wird erzeugt', icon: 'image' },
  edit_image: { label: 'Bild bearbeiten', activity: 'Bild wird bearbeitet', icon: 'auto_fix_high' },
  generate_speech: { label: 'Sprache erzeugen', activity: 'Sprachaufnahme wird erzeugt', icon: 'record_voice_over' },
  generate_audio: { label: 'Audio erzeugen', activity: 'Audio wird erzeugt', icon: 'graphic_eq' },
  transcribe_audio: { label: 'Audio transkribieren', activity: 'Audio wird transkribiert', icon: 'mic' },
  analyze_video: { label: 'Video analysieren', activity: 'Video wird analysiert', icon: 'video_file' },
  calendar_list_accounts: { label: 'Kalenderkonten auflisten', activity: 'Kalenderkonten werden geladen', icon: 'account_circle' },
  calendar_list_calendars: { label: 'Kalender auflisten', activity: 'Kalender werden geladen', icon: 'calendar_month' },
  calendar_query_events: { label: 'Termine lesen', activity: 'Termine werden gelesen', icon: 'event' },
  calendar_create_event: { label: 'Termin erstellen', activity: 'Termin wird erstellt', icon: 'event_available' },
  calendar_update_event: { label: 'Termin ändern', activity: 'Termin wird geändert', icon: 'edit_calendar' },
  calendar_delete_event: { label: 'Termin löschen', activity: 'Termin wird gelöscht', icon: 'event_busy' },
  tasks_list_accounts: { label: 'Aufgabenkonten auflisten', activity: 'Aufgabenkonten werden geladen', icon: 'account_circle' },
  tasks_list_lists: { label: 'Aufgabenlisten auflisten', activity: 'Aufgabenlisten werden geladen', icon: 'checklist' },
  tasks_query: { label: 'Aufgaben lesen', activity: 'Aufgaben werden gelesen', icon: 'task_alt' },
  tasks_create: { label: 'Aufgabe erstellen', activity: 'Aufgabe wird erstellt', icon: 'add_task' },
  tasks_create_many: { label: 'Fehlende Aufgaben erstellen', activity: 'Aufgaben werden erstellt', icon: 'playlist_add' },
  tasks_update: { label: 'Aufgabe ändern', activity: 'Aufgabe wird geändert', icon: 'edit_note' },
  tasks_set_completed: { label: 'Aufgabenstatus ändern', activity: 'Aufgabenstatus wird geändert', icon: 'task_alt' },
  tasks_delete: { label: 'Aufgabe löschen', activity: 'Aufgabe wird gelöscht', icon: 'delete_outline' },
  mealie_list_accounts: { label: 'Mealie-Konten auflisten', activity: 'Mealie-Konten werden geladen', icon: 'restaurant' },
  recipes_search: { label: 'Rezepte suchen', activity: 'Rezepte werden gesucht', icon: 'search' },
  recipe_get: { label: 'Rezept lesen', activity: 'Rezept wird gelesen', icon: 'menu_book' },
  meal_plan_get: { label: 'Essensplan lesen', activity: 'Essensplan wird gelesen', icon: 'calendar_month' },
  meal_plan_add: { label: 'Essensplan-Eintrag erstellen', activity: 'Essensplan-Eintrag wird erstellt', icon: 'event_available' },
  meal_plan_update: { label: 'Essensplan-Eintrag ändern', activity: 'Essensplan-Eintrag wird geändert', icon: 'edit_calendar' },
  meal_plan_delete: { label: 'Essensplan-Eintrag löschen', activity: 'Essensplan-Eintrag wird gelöscht', icon: 'event_busy' },
  session_read: { label: 'Session lesen', activity: 'Session wird gelesen', icon: 'forum' },
  session_tool_result_read: { label: 'Tool-Ergebnis lesen', activity: 'Tool-Ergebnis wird gelesen', icon: 'history' },
  session_browse: { label: 'Letzte Sessions anzeigen', activity: 'Letzte Sessions werden geladen', icon: 'view_list' },
  session_search: { label: 'Sessions durchsuchen', activity: 'Sessions werden durchsucht', icon: 'manage_search' },
  memory_retrieve: { label: 'Memory abrufen', activity: 'Memory wird abgerufen', icon: 'psychology' },
  memory_write: { label: 'Memory speichern', activity: 'Memory wird gespeichert', icon: 'save' },
  update_agent_identity: { label: 'Persona ändern', activity: 'Persona wird aktualisiert', icon: 'face' },
  process_exec: { label: 'Prozess ausführen', activity: 'Prozess wird ausgeführt', icon: 'terminal' },
  'process.exec': { label: 'Prozess ausführen', activity: 'Prozess wird ausgeführt', icon: 'terminal' },
  shell_run: { label: 'Shell-Befehl ausführen', activity: 'Befehl wird ausgeführt', icon: 'terminal' },
  'shell.run': { label: 'Shell-Befehl ausführen', activity: 'Befehl wird ausgeführt', icon: 'terminal' },
  git_clone: { label: 'Git-Repository klonen', activity: 'Git-Repository wird geklont', icon: 'account_tree' },
  git_status: { label: 'Git-Status anzeigen', activity: 'Git-Status wird gelesen', icon: 'fact_check' },
  git_diff: { label: 'Git-Änderungen anzeigen', activity: 'Git-Änderungen werden gelesen', icon: 'difference' },
  git_log: { label: 'Git-Verlauf anzeigen', activity: 'Git-Verlauf wird gelesen', icon: 'history' },
  git_add: { label: 'Git-Änderungen vormerken', activity: 'Git-Änderungen werden vorgemerkt', icon: 'add_task' },
  git_commit: { label: 'Git-Commit erstellen', activity: 'Git-Commit wird erstellt', icon: 'commit' }
}

function truncate(value, maxLength = 140) {
  const text = String(value || '').trim().replace(/\s+/g, ' ')
  return text.length > maxLength ? `${text.slice(0, maxLength)}...` : text
}

export function toolIdentity(name) {
  const display = TOOL_DISPLAY[name]
  return display ? { label: display.label, icon: display.icon } : { label: 'Tool ausführen', icon: 'build' }
}

export function toolActivityLabel(name) {
  return TOOL_DISPLAY[name]?.activity || 'Tool wird ausgeführt'
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

export function shellCommand(tool) {
  if (!['shell_run', 'shell.run'].includes(tool?.name)) return ''
  return typeof tool?.arguments?.command === 'string' ? tool.arguments.command : ''
}

function taskDisplayItems(tool) {
  const args = tool?.arguments || {}
  const has = (key) => Object.prototype.hasOwnProperty.call(args, key)
  if (tool?.name === 'tasks_query') {
    return [
      { label: 'Aktion', value: 'Aufgaben lesen', format: 'text' },
      { label: 'Anzahl', value: `Bis zu ${args.limit || 100}`, format: 'text' },
      {
        label: 'Status',
        value: args.include_completed ? 'Offene und erledigte Aufgaben' : 'Nur offene Aufgaben',
        format: 'text'
      }
    ]
  }
  if (tool?.name === 'tasks_create') {
    return [
      { label: 'Aktion', value: 'Aufgabe erstellen', format: 'text' },
      ...(typeof args.title === 'string'
        ? [{ label: 'Aufgabe', value: args.title, format: 'text' }]
        : []),
      { label: 'Fälligkeit', value: args.due || 'Keine Fälligkeit', format: 'text' },
      ...(typeof args.description === 'string' && args.description
        ? [{ label: 'Notiz', value: args.description, format: 'text' }]
        : [])
    ]
  }
  if (tool?.name === 'tasks_create_many') {
    return [
      { label: 'Aktion', value: 'Fehlende Aufgaben erstellen', format: 'text' },
      {
        label: 'Aufgaben',
        value: Array.isArray(args.tasks) ? args.tasks.map((item) => item?.title).filter(Boolean) : [],
        format: 'list'
      }
    ]
  }
  if (tool?.name === 'tasks_update') {
    const changes = [
      ...(has('title') ? [`Titel: ${args.title}`] : []),
      ...(has('description') ? [args.description ? `Notiz: ${args.description}` : 'Notiz entfernen'] : []),
      ...(has('due') ? [args.due ? `Fälligkeit: ${args.due}` : 'Fälligkeit entfernen'] : [])
    ]
    return [
      { label: 'Aktion', value: 'Aufgabe ändern', format: 'text' },
      ...(changes.length ? [{ label: 'Änderungen', value: changes, format: 'list' }] : [])
    ]
  }
  if (tool?.name === 'tasks_set_completed') {
    return [
      { label: 'Aktion', value: 'Aufgabenstatus ändern', format: 'text' },
      { label: 'Neuer Status', value: args.completed ? 'Erledigt' : 'Offen', format: 'text' }
    ]
  }
  if (tool?.name === 'tasks_delete') {
    return [{ label: 'Aktion', value: 'Aufgabe löschen', format: 'text' }]
  }
  return []
}

function normalizeDisplayItems(items) {
  if (!Array.isArray(items)) return []
  return items.flatMap((item) => {
    if (!item || typeof item.label !== 'string' || !['text', 'code', 'list'].includes(item.format)) return []
    const value = item.format === 'list'
      ? Array.isArray(item.value) ? item.value.map(String) : []
      : typeof item.value === 'string' ? item.value : ''
    return item.format === 'list' || value.length
      ? [{ label: item.label, value, format: item.format }]
      : []
  })
}

export function toolPresentation(tool, displayItems = []) {
  const suppliedItems = normalizeDisplayItems(displayItems)
  if (suppliedItems.length) return { summary: toolSummary(tool), items: suppliedItems }

  if (tool?.name === 'git_clone') {
    const source = tool.arguments?.source || tool.arguments?.repository_url || tool.arguments?.url
    const isUrl = /^https?:\/\//i.test(source)
    return {
      summary: toolSummary(tool),
      items: typeof source === 'string' && source
        ? [{
            label: isUrl ? 'Repository-URL' : 'Repository-Quelle',
            value: isUrl ? cleanUrlForDisplay(source) : source,
            format: 'code'
          }]
        : []
    }
  }

  const command = shellCommand(tool)
  if (command) {
    return {
      summary: toolSummary(tool),
      items: [{ label: 'Shell-Befehl', value: command, format: 'code' }]
    }
  }
  if (['process_exec', 'process.exec'].includes(tool?.name)) {
    const args = tool?.arguments || {}
    return {
      summary: toolSummary(tool),
      items: [
        ...(typeof args.program === 'string'
          ? [{ label: 'Programm', value: args.program, format: 'code' }]
          : []),
        { label: 'Parameter', value: Array.isArray(args.args) ? args.args.map(String) : [], format: 'list' }
      ]
    }
  }
  const memory = memoryWriteContent(tool)
  if (memory) {
    return {
      summary: toolSummary(tool),
      items: [{ label: 'Memory-Inhalt', value: memory, format: 'code' }]
    }
  }
  return { summary: toolSummary(tool), items: taskDisplayItems(tool) }
}

export function toolSummary(tool) {
  const argumentsValue = tool?.arguments
  const fallback = TOOL_DISPLAY[tool?.name] ? '' : tool?.name || ''
  if (!argumentsValue || typeof argumentsValue !== 'object') return fallback
  if (tool?.name === 'question') {
    return truncate(argumentsValue.questions?.find((item) => typeof item?.question === 'string')?.question)
  }
  if (tool?.name === 'memory_retrieve' && argumentsValue.mode === 'browse') {
    return argumentsValue.cursor_id ? 'Memory-Übersicht fortsetzen' : 'Memory-Übersicht'
  }
  if (tool?.name === 'session_browse') return 'Letzte Sessions'
  if (['shell_run', 'shell.run'].includes(tool?.name)) {
    return truncate(argumentsValue.command)
  }
  if (['process_exec', 'process.exec'].includes(tool?.name)) {
    const program = typeof argumentsValue.program === 'string' ? argumentsValue.program : ''
    const args = Array.isArray(argumentsValue.args) ? argumentsValue.args.join(', ') : ''
    return truncate([program, args].filter(Boolean).join(' · '))
  }
  if (tool?.name === 'tasks_create' && typeof argumentsValue.title === 'string') {
    return truncate(argumentsValue.title)
  }
  if (tool?.name === 'tasks_create_many' && Array.isArray(argumentsValue.tasks)) {
    return `${argumentsValue.tasks.length} Aufgaben`
  }
  if (tool?.name === 'meal_plan_get') {
    return truncate([argumentsValue.start_date, argumentsValue.end_date].filter(Boolean).join(' – '))
  }
  if (tool?.name === 'meal_plan_add') {
    return truncate(argumentsValue.title || argumentsValue.text || argumentsValue.date || 'Eintrag erstellen')
  }
  if (tool?.name === 'meal_plan_update') return truncate(argumentsValue.title || 'Eintrag ändern')
  if (tool?.name === 'meal_plan_delete') return 'Eintrag löschen'
  if (tool?.name === 'tasks_query') {
    return argumentsValue.include_completed ? 'Offene und erledigte Aufgaben' : 'Offene Aufgaben'
  }
  if (tool?.name === 'tasks_update') {
    return typeof argumentsValue.title === 'string' ? truncate(argumentsValue.title) : 'Aufgabe ändern'
  }
  if (tool?.name === 'tasks_set_completed') {
    return argumentsValue.completed ? 'Als erledigt markieren' : 'Als offen markieren'
  }
  if (tool?.name === 'tasks_delete') return 'Aufgabe löschen'
  if (tool?.name === 'git_clone') {
    const url = argumentsValue.source || argumentsValue.repository_url || argumentsValue.url
    const destination = argumentsValue.destination || argumentsValue.target_path || argumentsValue.path
    return truncate([url ? cleanUrlForDisplay(url) : '', destination].filter(Boolean).join(' -> '))
  }
  if (tool?.name === 'git_add' && Array.isArray(argumentsValue.paths)) {
    return truncate(argumentsValue.paths.join(', '))
  }
  if (['git_diff', 'git_log'].includes(tool?.name) && Array.isArray(argumentsValue.paths)) {
    const paths = truncate(argumentsValue.paths.join(', '))
    if (paths) return tool.name === 'git_diff' && argumentsValue.staged ? `Vorgemerkt: ${paths}` : paths
    if (tool.name === 'git_diff' && argumentsValue.staged) return 'Vorgemerkte Änderungen'
    if (tool.name === 'git_log' && Number.isInteger(argumentsValue.limit)) {
      return `Letzte ${argumentsValue.limit} Commits`
    }
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
  if (tool?.name === 'session_read') {
    return typeof argumentsValue.session_id === 'string'
      ? `Session ${truncate(argumentsValue.session_id, 36)}`
      : 'Aktuelle Session'
  }
  if (tool?.name === 'calendar_query_events') {
    return truncate([argumentsValue.start, argumentsValue.end].filter(Boolean).join(' – '))
  }
  if (['calendar_create_event', 'calendar_update_event'].includes(tool?.name)) {
    if (typeof argumentsValue.summary === 'string') return truncate(argumentsValue.summary)
    return truncate([argumentsValue.timing?.start, argumentsValue.timing?.end].filter(Boolean).join(' – '))
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
  if (typeof argumentsValue.path === 'string') {
    const root = typeof argumentsValue.root === 'string' ? `${argumentsValue.root}:` : ''
    return truncate(`${root}${argumentsValue.path}`)
  }
  if (typeof argumentsValue.source_path === 'string' && typeof argumentsValue.destination_path === 'string') {
    const sourceRoot = argumentsValue.source_root ? `${argumentsValue.source_root}:` : ''
    const destinationRoot = argumentsValue.destination_root ? `${argumentsValue.destination_root}:` : ''
    return truncate(`${sourceRoot}${argumentsValue.source_path} -> ${destinationRoot}${argumentsValue.destination_path}`)
  }
  if (typeof argumentsValue.url === 'string') return cleanUrlForDisplay(argumentsValue.url)
  if (typeof argumentsValue.query === 'string') return truncate(argumentsValue.query)
  if (Array.isArray(argumentsValue.queries)) {
    const queries = argumentsValue.queries.filter((value) => typeof value === 'string' && value)
    if (queries.length) return truncate(queries.join(' · '))
  }
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
