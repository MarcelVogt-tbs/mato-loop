import { describe, expect, it } from 'vitest'

import {
  approvalPresentationKind,
  memoryWriteContent,
  parseToolResult,
  shellCommand,
  toolArgumentsDisplay,
  toolActivityLabel,
  toolIdentity,
  toolPresentation,
  toolStatus,
  toolSummary,
  toolUsageSummary
} from '../src/utils/tool-display'

describe('tool display', () => {
  it('shows shell commands exactly as requested', () => {
    const command = 'set -e\nprintf "%s\\n" "hello world"'

    expect(shellCommand({ name: 'shell_run', arguments: { command } })).toBe(command)
    expect(shellCommand({ name: 'shell.run', arguments: { command } })).toBe(command)
    expect(shellCommand({ name: 'process_exec', arguments: { command } })).toBe('')
    expect(approvalPresentationKind({ name: 'shell_run', arguments: { command } })).toBe('shell')
    expect(approvalPresentationKind({ name: 'shell.run', arguments: { command } })).toBe('shell')
    expect(toolPresentation({ name: 'shell_run', arguments: { command } })).toEqual({
      summary: 'set -e printf "%s\\n" "hello world"',
      items: [{ label: 'Shell-Befehl', value: command, format: 'code' }]
    })
  })

  it('falls back to generic approval presentation for unknown or incomplete tools', () => {
    expect(approvalPresentationKind({ name: 'tasks_create', arguments: { title: 'Test' } })).toBeNull()
    expect(approvalPresentationKind({ name: 'shell_run', arguments: {} })).toBeNull()
  })

  it('presents process arguments without rebuilding a shell command', () => {
    expect(toolPresentation({
      name: 'process_exec',
      arguments: { program: 'printf', args: ['%s', 'hello world'] }
    })).toEqual({
      summary: 'printf · %s, hello world',
      items: [
        { label: 'Programm', value: 'printf', format: 'code' },
        { label: 'Parameter', value: ['%s', 'hello world'], format: 'list' }
      ]
    })
  })

  it('uses resolved approval display items for CalDAV tasks', () => {
    const tool = {
      name: 'tasks_update',
      arguments: { task_token: '[Redacted]', title: 'Neuer Titel' }
    }
    const displayItems = [
      { label: 'Aktion', value: 'Aufgabe ändern', format: 'text' },
      { label: 'Aufgabe', value: 'Bericht abgeben', format: 'text' },
      { label: 'Änderungen', value: ['Titel: Neuer Titel'], format: 'list' }
    ]

    expect(toolPresentation(tool, displayItems)).toEqual({
      summary: 'Neuer Titel',
      items: displayItems
    })
    expect(toolPresentation(tool).items).toEqual([
      { label: 'Aktion', value: 'Aufgabe ändern', format: 'text' },
      { label: 'Änderungen', value: ['Titel: Neuer Titel'], format: 'list' }
    ])
  })

  it('labels Mealie and batch shopping-list tools', () => {
    expect(toolIdentity('recipes_search')).toEqual({ label: 'Rezepte suchen', icon: 'search' })
    expect(toolIdentity('meal_plan_get')).toEqual({ label: 'Essensplan lesen', icon: 'calendar_month' })
    expect(toolSummary({
      name: 'meal_plan_get',
      arguments: { start_date: '2026-08-17', end_date: '2026-08-23' }
    })).toBe('2026-08-17 – 2026-08-23')
    expect(toolPresentation({
      name: 'tasks_create_many',
      arguments: { tasks: [{ title: 'Milch' }, { title: 'Brot' }] }
    })).toEqual({
      summary: '2 Aufgaben',
      items: [
        { label: 'Aktion', value: 'Fehlende Aufgaben erstellen', format: 'text' },
        { label: 'Aufgaben', value: ['Milch', 'Brot'], format: 'list' }
      ]
    })
  })

  it('uses readable labels and concise resource summaries', () => {
    expect(toolIdentity('read_file')).toEqual({ label: 'Datei lesen', icon: 'description' })
    expect(toolIdentity('read')).toEqual({ label: 'Datei lesen', icon: 'description' })
    expect(toolIdentity('glob')).toEqual({ label: 'Dateien suchen', icon: 'folder_open' })
    expect(toolIdentity('grep')).toEqual({ label: 'Dateiinhalte durchsuchen', icon: 'manage_search' })
    expect(toolIdentity('list_directory')).toEqual({ label: 'Ordner auflisten', icon: 'folder_open' })
    expect(toolIdentity('provide_file')).toEqual({ label: 'Datei bereitstellen', icon: 'download' })
    expect(toolIdentity('edit_file')).toEqual({ label: 'Datei bearbeiten', icon: 'edit_note' })
    expect(toolSummary({ name: 'read_file', arguments: { path: 'Projekte/plan.md' } })).toBe('Projekte/plan.md')
    expect(toolSummary({ name: 'read_file', arguments: {} })).toBe('')
    expect(toolSummary({ name: 'read', arguments: { root: 'workspace', path: 'Projekte/plan.md' } }))
      .toBe('workspace:Projekte/plan.md')
    expect(toolSummary({
      name: 'glob',
      arguments: { root: 'workspace', path: 'src', pattern: '**/*.vue' }
    })).toBe('workspace:src · **/*.vue')
    expect(toolSummary({
      name: 'grep',
      arguments: { root: 'session', path: '.', pattern: 'TODO|FIXME' }
    })).toBe('session:. · TODO|FIXME')
    expect(toolSummary({ name: 'custom_tool', arguments: {} })).toBe('custom_tool')
    expect(toolIdentity('copy_path')).toEqual({ label: 'Pfad kopieren', icon: 'content_copy' })
    expect(toolIdentity('move_path')).toEqual({ label: 'Pfad verschieben', icon: 'drive_file_move' })
    expect(toolIdentity('update_agent_identity')).toEqual({ label: 'Persona ändern', icon: 'face' })
    expect(toolIdentity('session_tool_result_read'))
      .toEqual({ label: 'Tool-Ergebnis lesen', icon: 'history' })
    expect(toolIdentity('session_browse'))
      .toEqual({ label: 'Letzte Sessions anzeigen', icon: 'view_list' })
    expect(toolIdentity('calendar_query_events'))
      .toEqual({ label: 'Termine lesen', icon: 'event' })
    expect([
      'tasks_list_accounts',
      'tasks_list_lists',
      'tasks_query',
      'tasks_create',
      'tasks_update',
      'tasks_set_completed',
      'tasks_delete'
    ].map((name) => toolIdentity(name))).toEqual([
      { label: 'Aufgabenkonten auflisten', icon: 'account_circle' },
      { label: 'Aufgabenlisten auflisten', icon: 'checklist' },
      { label: 'Aufgaben lesen', icon: 'task_alt' },
      { label: 'Aufgabe erstellen', icon: 'add_task' },
      { label: 'Aufgabe ändern', icon: 'edit_note' },
      { label: 'Aufgabenstatus ändern', icon: 'task_alt' },
      { label: 'Aufgabe löschen', icon: 'delete_outline' }
    ])
    expect(toolSummary({ name: 'session_browse', arguments: {} })).toBe('Letzte Sessions')
    expect(toolActivityLabel('web_search')).toBe('Websuche')
    expect(toolActivityLabel('webfetch')).toBe('Webseite abrufen')
    expect(toolIdentity('fetch_transcript')).toEqual({ label: 'Untertitel abrufen', icon: 'subtitles' })
    expect(toolActivityLabel('fetch_transcript')).toBe('Untertitel abrufen')
    expect(toolActivityLabel('write_file')).toBe('Datei schreiben')
    expect(toolActivityLabel('edit_file')).toBe('Datei bearbeiten')
    expect(toolActivityLabel('shell_run')).toBe('Shell-Befehl ausführen')
    expect(toolActivityLabel('unknown_tool')).toBe('Tool ausführen')
    expect(toolIdentity('create_automation')).toEqual({ label: 'Automation erstellen', icon: 'add_task' })
    expect(toolActivityLabel('run_automation')).toBe('Automation starten')
    expect(toolSummary({ name: 'create_automation', arguments: { name: 'Morgenbericht' } })).toBe('Morgenbericht')
    expect(toolSummary({ name: 'web_search', arguments: { query: 'Quasar Expansion Item' } }))
      .toBe('Quasar Expansion Item')
    expect(toolSummary({
      name: 'write_file', arguments: { root: 'workspace', path: 'docs/plan.md', content: 'secret' }
    })).toBe('workspace:docs/plan.md')
    expect(toolSummary({
      name: 'edit_file', arguments: { root: 'workspace', path: 'docs/plan.md' }
    })).toBe('workspace:docs/plan.md')
    expect(toolSummary({
      name: 'copy_path',
      arguments: {
        source_root: 'session',
        source_path: 'generated/bild.png',
        destination_root: 'workspace',
        destination_path: 'bild.png'
      }
    })).toBe('session:generated/bild.png -> workspace:bild.png')
    expect(toolSummary({
      name: 'session_tool_result_read',
      arguments: { message_id: '550e8400-e29b-41d4-a716-446655440000', offset: 12000 }
    })).toBe('Nachricht 550e8400-e29b-41d4-a716-446655440000, ab Zeichen 12000')
  })

  it('groups tool usage by group while preserving first-use order', () => {
    expect(toolUsageSummary([
      { tool: { name: 'web_search' } },
      { tool: { name: 'webfetch' }, active: true },
      { tool: { name: 'web_search' }, active: true }
    ])).toEqual([
      { group: 'web', label: 'Web', icon: 'language', count: 3, active: true }
    ])
  })

  it('combines argument-specific actions in their shared tool group', () => {
    expect(toolUsageSummary([
      { tool: { name: 'browser_tabs', toolDisplay: { group: 'web', icon: 'language', label: 'Browser-Tabs anzeigen' } } },
      { tool: { name: 'browser_tabs', toolDisplay: { group: 'web', icon: 'language', label: 'Browser-Tab schließen' } } }
    ])).toEqual([
      { group: 'web', label: 'Web', icon: 'language', count: 2, active: false }
    ])
  })

  it('groups legacy host tools like current backend metadata', () => {
    expect(toolUsageSummary([
      { tool: { name: 'get_system_info' } },
      { tool: { name: 'change_directory' } }
    ])).toEqual([
      { group: 'process', label: 'Prozesse', icon: 'terminal', count: 2, active: false }
    ])
  })

  it('prefers valid backend metadata and keeps browser family icons consistent', () => {
    const enriched = {
      name: 'custom_search',
      arguments: { query: 'legacy query' },
      toolDisplay: { group: 'web', icon: 'language', label: 'Wissen durchsuchen', summary: 'Kanonische Zusammenfassung' }
    }
    expect(toolIdentity(enriched)).toEqual({ label: 'Wissen durchsuchen', icon: 'language' })
    expect(toolSummary(enriched)).toBe('Kanonische Zusammenfassung')
    expect(toolSummary({
      name: 'shell_run',
      arguments: { command: 'cat /private/secret' },
      toolDisplay: { group: 'process', icon: 'terminal', label: 'Shell-Befehl ausführen' }
    })).toBe('')
    expect(toolIdentity({
      name: 'read_file',
      toolDisplay: { label: '  ', icon: null, summary: {} }
    })).toEqual({ label: 'Datei lesen', icon: 'description' })

    const usage = toolUsageSummary([
      { tool: { name: 'browser_snapshot' } },
      { tool: { name: 'browser_click' } },
      { tool: { name: 'browser_snapshot' }, active: true }
    ])
    expect(usage).toEqual([
      { group: 'web', label: 'Web', icon: 'language', count: 3, active: true }
    ])
  })

  it('uses generic presentation for unrecognized historical tools', () => {
    const tool = { name: 'retired_tool', arguments: { path: 'project' } }
    expect(toolIdentity(tool.name)).toEqual({ label: 'Tool ausführen', icon: 'build' })
    expect(toolPresentation(tool)).toEqual({
      summary: 'project', items: [{ label: 'Pfad', value: 'project', format: 'text' }]
    })
    expect(toolPresentation({ name: tool.name, arguments: {} }))
      .toEqual({ summary: tool.name, items: [] })
    const items = [{ label: 'Aktion', value: 'Gespeicherte Aktion', format: 'text' }]
    expect(toolPresentation(tool, items)).toEqual({ summary: 'project', items })
  })

  it('summarizes persona changes without rendering the complete instructions', () => {
    expect(toolSummary({
      name: 'update_agent_identity',
      arguments: { display_name: 'Ada', instructions: 'Eine lange Persona' }
    })).toBe('Agent-Name: Ada')
    expect(toolSummary({
      name: 'update_agent_identity',
      arguments: { instructions: 'Eine lange Persona' }
    })).toBe('Verhalten und Charakter aktualisieren')
  })

  it('presents personal profile changes with their model scope', () => {
    const tool = {
      name: 'update_user_profile',
      arguments: {
        model_scope: 'both_models',
        full_name: 'Lara Beispiel',
        preferred_name: 'Lara',
        primary_residence: null
      }
    }

    expect(toolIdentity(tool.name)).toEqual({
      label: 'Persönliches Profil ändern',
      icon: 'manage_accounts'
    })
    expect(toolPresentation(tool)).toEqual({
      summary: '3 Profilfelder',
      items: [
        { label: 'Gilt für', value: 'Private und öffentliche Modelle', format: 'text' },
        { label: 'Vollständiger Name', value: 'Lara Beispiel', format: 'text' },
        { label: 'Bevorzugter Name', value: 'Lara', format: 'text' },
        { label: 'Hauptwohnsitz', value: 'Entfernen', format: 'text' }
      ]
    })
  })

  it('identifies webfetch exactly and hides sensitive URL parameters', () => {
    expect(toolIdentity('webfetch')).toEqual({ label: 'Webseite abrufen', icon: 'language' })
    expect(toolSummary({
      name: 'webfetch',
      arguments: { url: 'https://user:secret@example.com/private?token=secret' }
    })).toBe('https://example.com/private?…')
  })

  it('uses a safe URL summary for historical browser navigation', () => {
    expect(toolSummary({
      name: 'browser_navigate',
      arguments: { url: 'https://user:secret@example.com/private?token=secret' }
    })).toBe('https://example.com/private?…')
  })

  it('summarizes calendar, task and session resources without exposing tokens', () => {
    expect(toolSummary({
      name: 'calendar_query_events',
      arguments: { start: '2026-08-07T14:00:00+02:00', end: '2026-08-07T15:00:00+02:00' }
    })).toBe('2026-08-07T14:00:00+02:00 – 2026-08-07T15:00:00+02:00')
    expect(toolSummary({
      name: 'calendar_create_event',
      arguments: { summary: 'Projektbesprechung', event_token: 'secret' }
    })).toBe('Projektbesprechung')
    expect(toolSummary({ name: 'tasks_query', arguments: { include_completed: false } }))
      .toBe('Offene Aufgaben')
    expect(toolSummary({ name: 'session_read', arguments: {} })).toBe('Aktuelle Session')
  })

  it('summarizes knowledge searches with every search term', () => {
    expect(toolSummary({
      name: 'knowledge_search',
      arguments: { queries: ['Netzwerk', 'VLAN'], limit: 20 }
    })).toBe('Netzwerk · VLAN')
    expect(toolSummary({ name: 'knowledge_search', arguments: { queries: [] } }))
      .toBe('knowledge_search')
  })

  it('labels media tools and summarizes their relevant arguments', () => {
    expect(toolIdentity('generate_qr_code')).toEqual({ label: 'QR-Code erzeugen', icon: 'qr_code_2' })
    expect(toolIdentity('read_qr_code')).toEqual({ label: 'QR-Code lesen', icon: 'qr_code_scanner' })
    expect(toolIdentity('generate_image')).toEqual({ label: 'Bild erzeugen', icon: 'image' })
    expect(toolIdentity('edit_image')).toEqual({ label: 'Bild bearbeiten', icon: 'auto_fix_high' })
    expect(toolIdentity('generate_speech')).toEqual({ label: 'Sprache erzeugen', icon: 'record_voice_over' })
    expect(toolIdentity('generate_audio')).toEqual({ label: 'Audio erzeugen', icon: 'graphic_eq' })
    expect(toolIdentity('transcribe_audio')).toEqual({ label: 'Audio transkribieren', icon: 'mic' })
    expect(toolIdentity('analyze_video')).toEqual({ label: 'Video analysieren', icon: 'video_file' })
    expect(toolSummary({ name: 'generate_image', arguments: { prompt: 'Ein ruhiger See' } }))
      .toBe('Ein ruhiger See')
    expect(toolSummary({ name: 'generate_qr_code', arguments: { content: 'vertraulich' } }))
      .toBe('QR-Code')
    expect(toolSummary({
      name: 'read_qr_code',
      arguments: { attachment_id: '550e8400-e29b-41d4-a716-446655440000' }
    })).toBe('Bildanhang 550e8400-e29b-41d4-a716-446655440000')
    expect(toolArgumentsDisplay({ content: 'vertraulich' }, 'generate_qr_code').text)
      .not.toContain('vertraulich')
    expect(toolSummary({ name: 'edit_image', arguments: { prompt: 'Farben restaurieren' } }))
      .toBe('Farben restaurieren')
    expect(toolSummary({
      name: 'generate_audio',
      arguments: { prompt: 'Leises Meeresrauschen', duration_seconds: 12 }
    })).toBe('Leises Meeresrauschen (12 s)')
    expect(toolSummary({
      name: 'transcribe_audio',
      arguments: { attachment_id: '550e8400-e29b-41d4-a716-446655440000' }
    })).toBe('Audioanhang 550e8400-e29b-41d4-a716-446655440000')
    expect(toolSummary({
      name: 'analyze_video',
      arguments: { root: 'session', path: 'attachments/example/video.mp4' }
    })).toBe('session:attachments/example/video.mp4')
  })

  it('localizes plain host-controlled tool results', () => {
    expect(parseToolResult('QR code generated.', 'generate_qr_code').text).toBe('QR-Code erzeugt.')
    expect(parseToolResult(
      'Image generated.\n\n[Image saved but not sent to the current text-only model.]',
      'generate_image'
    ).text).toBe('Bild erzeugt.\n\n[Bild gespeichert, aber nicht an das aktuelle Textmodell übertragen.]')
    expect(parseToolResult(
      '2: two\n\n[More lines are available. Continue with offset=3.]',
      'read_file'
    ).text).toContain('[Weitere Zeilen verfügbar. Mit offset=3 weiterlesen.]')
    expect(parseToolResult(
      '2: two\n\n[More lines are available. Continue with offset=3.]',
      'read'
    ).text).toContain('[Weitere Zeilen verfügbar. Mit offset=3 weiterlesen.]')
    expect(parseToolResult(
      '1: [More lines are available. Continue with offset=3.]',
      'read_file'
    ).text).toBe('1: [More lines are available. Continue with offset=3.]')
  })

  it('separates process stdout and stderr from result metadata', () => {
    const result = parseToolResult(JSON.stringify({
      stdout: 'Ausgabe\n',
      stderr: 'Warnung\n',
      exitCode: 0,
      truncated: true
    }), 'process_exec')

    expect(toolIdentity('process_exec')).toEqual({ label: 'Prozess ausführen', icon: 'terminal' })
    expect(result).toMatchObject({
      stdout: 'Ausgabe\n',
      stderr: 'Warnung\n',
      truncated: true
    })
    expect(result.text).toContain('exitCode')
    expect(result.text).not.toContain('Ausgabe')
  })

  it('does not duplicate process output when no metadata exists', () => {
    const result = parseToolResult(JSON.stringify({ stdout: 'Ausgabe\n', stderr: '' }), 'process_exec')

    expect(result.text).toBe('')
    expect(result.stdout).toBe('Ausgabe\n')
  })

  it('uses the returned session title as the safe session-read summary', () => {
    expect(parseToolResult(JSON.stringify({
      session: { id: '51680cd9-ff48-448c-a4bc-089896f7257d', title: 'Lokale KI recherchieren' },
      messages: []
    }), 'session_read').summary).toBe('Lokale KI recherchieren')
  })

  it('renders memory retrieval with its visible scope and contents', () => {
    expect(toolIdentity('memory_retrieve')).toEqual({ label: 'Memory abrufen', icon: 'psychology' })
    expect(toolSummary({
      name: 'memory_retrieve',
      arguments: { mode: 'browse', cursor_id: 'memory-0' }
    })).toBe('Memory-Übersicht fortsetzen')

    const parsed = parseToolResult(JSON.stringify({
      mode: 'browse',
      scope: { readableBy: 'private' },
      total: 1,
      nextCursor: { cursor_updated_at: '2026-07-23T12:00:00Z', cursor_id: 'memory-1' },
      items: [{
        id: 'memory-1', content: 'Sichtbarer Inhalt', category: 'identity', pinned: true,
        audience: 'private_only'
      }]
    }), 'memory_retrieve')

    expect(parsed.text).toContain('Sichtbarer Inhalt')
    expect(parsed.text).toContain('private')
    expect(parsed.text).toContain('pinned')
    expect(parsed.text).toContain('private_only')
    expect(parsed.text).toContain('cursor_updated_at')
  })

  it('shows the complete memory write content without exposing it as generic metadata', () => {
    const content = 'Dauerhafter Kontext. '.repeat(1000)
    const tool = {
      name: 'memory_write',
      arguments: { action: 'create', content, category: 'context' }
    }

    expect(memoryWriteContent(tool)).toBe(content)
    expect(toolSummary(tool)).toContain('Dauerhafter Kontext')
    expect(toolArgumentsDisplay(tool.arguments, tool.name).text).not.toContain(content)
    expect(toolArgumentsDisplay(tool.arguments, tool.name).text).toContain('context')
  })

  it('shows persisted legacy memory previews', () => {
    const content = 'Kurzes vollständiges Memory'
    expect(memoryWriteContent({
      name: 'memory_write',
      arguments: { content: { preview: content, characters: content.length } }
    })).toBe(content)
  })

  it('redacts sensitive arguments and bounds their output', () => {
    const formatted = toolArgumentsDisplay({ path: 'plan.md', content: 'vertraulich', token: 'secret' }).text
    expect(formatted).toContain('plan.md')
    expect(formatted).not.toContain('vertraulich')
    expect(formatted).not.toContain('secret')
  })

  it('provides complete long arguments after a bounded preview', () => {
    const prompt = 'EDIT Image 1. '.repeat(100)
    const display = toolArgumentsDisplay({ prompt }, 'edit_image')

    expect(display.truncated).toBe(true)
    expect(display.text).toContain('[gekürzt]')
    expect(JSON.parse(display.fullText).prompt).toBe(prompt)
  })

  it('provides complete long plain tool results after a bounded preview', () => {
    const content = 'Ausgabe '.repeat(1000)
    const result = parseToolResult(content)

    expect(result.textTruncated).toBe(true)
    expect(result.text).toContain('[Ergebnis gekürzt]')
    expect(result.fullText).toBe(content)
  })

  it('translates structured tool errors and derives their status', () => {
    const result = {
      content: JSON.stringify({
        error: {
          code: 'provider.timeout',
          params: { timeoutSeconds: 300, retryable: true }
        }
      })
    }
    expect(parseToolResult(result.content).errorMessage)
      .toBe('Der Provider hat nicht innerhalb von 300 Sekunden geantwortet.')
    expect(toolStatus({ result })).toMatchObject({ name: 'failed', color: 'negative' })
  })

  it('distinguishes running, approval and successful calls', () => {
    expect(toolStatus({ active: true }).name).toBe('running')
    expect(toolStatus({ approval: { id: 'approval-1' } }).name).toBe('approval')
    expect(toolStatus({ result: { content: '{"written":"plan.md"}' } }).name).toBe('completed')
  })
})
