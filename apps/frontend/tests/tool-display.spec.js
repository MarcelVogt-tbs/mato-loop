import { describe, expect, it } from 'vitest'

import {
  formatToolArguments,
  memoryWriteContent,
  parseToolResult,
  shellCommand,
  toolArgumentsDisplay,
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
    expect(toolPresentation({ name: 'shell_run', arguments: { command } })).toEqual({
      summary: 'set -e printf "%s\\n" "hello world"',
      items: [{ label: 'Shell-Befehl', value: command, format: 'code' }]
    })
  })

  it('presents process arguments without rebuilding a shell command', () => {
    expect(toolPresentation({
      name: 'process_exec',
      arguments: { program: 'git', args: ['commit', '-m', 'hello world'] }
    })).toEqual({
      summary: 'git · commit, -m, hello world',
      items: [
        { label: 'Programm', value: 'git', format: 'code' },
        { label: 'Parameter', value: ['commit', '-m', 'hello world'], format: 'list' }
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
      summary: 'Aufgabe ändern',
      items: displayItems
    })
    expect(toolPresentation(tool).items).toEqual([
      { label: 'Aktion', value: 'Aufgabe ändern', format: 'text' },
      { label: 'Änderungen', value: ['Titel: Neuer Titel'], format: 'list' }
    ])
  })

  it('uses readable labels and concise resource summaries', () => {
    expect(toolIdentity('read_file')).toEqual({ label: 'Datei lesen', icon: 'description' })
    expect(toolIdentity('read')).toEqual({ label: 'Datei lesen', icon: 'description' })
    expect(toolIdentity('glob')).toEqual({ label: 'Dateien suchen', icon: 'folder_open' })
    expect(toolIdentity('grep')).toEqual({ label: 'Dateiinhalte durchsuchen', icon: 'manage_search' })
    expect(toolIdentity('list_directory')).toEqual({ label: 'Ordner auflisten', icon: 'folder_open' })
    expect(toolIdentity('provide_file')).toEqual({ label: 'Datei bereitstellen', icon: 'download' })
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
      arguments: { root: 'session', path: 'files', pattern: 'TODO|FIXME' }
    })).toBe('session:files · TODO|FIXME')
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

  it('groups tool usage by name while preserving first-use order', () => {
    expect(toolUsageSummary([
      { tool: { name: 'web_search' } },
      { tool: { name: 'webfetch' }, active: true },
      { tool: { name: 'web_search' }, active: true }
    ])).toEqual([
      { name: 'web_search', label: 'Im Web suchen', icon: 'travel_explore', count: 2, active: true },
      { name: 'webfetch', label: 'Webseite abrufen', icon: 'language', count: 1, active: true }
    ])
  })

  it('labels and summarizes Git tools in German', () => {
    expect([
      'git_clone',
      'git_status',
      'git_diff',
      'git_log',
      'git_add',
      'git_commit'
    ].map((name) => toolIdentity(name).label)).toEqual([
      'Git-Repository klonen',
      'Git-Status anzeigen',
      'Git-Änderungen anzeigen',
      'Git-Verlauf anzeigen',
      'Git-Änderungen vormerken',
      'Git-Commit erstellen'
    ])
    expect(toolSummary({
      name: 'git_clone',
      arguments: {
        repository_url: 'https://user:secret@example.com/team/project.git?token=secret',
        destination: 'project'
      }
    })).toBe('https://example.com/team/project.git?… -> project')
    expect(toolSummary({ name: 'git_status', arguments: { path: 'project' } })).toBe('project')
    expect(toolSummary({ name: 'git_diff', arguments: { repository_path: 'project' } })).toBe('project')
    expect(toolSummary({ name: 'git_log', arguments: { repositoryPath: 'project' } })).toBe('project')
    expect(toolSummary({ name: 'git_add', arguments: { paths: ['src/app.js', 'README.md'] } }))
      .toBe('src/app.js, README.md')
    expect(toolSummary({ name: 'git_commit', arguments: { message: 'Git-Anzeige ergänzen' } }))
      .toBe('Git-Anzeige ergänzen')
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

  it('identifies webfetch exactly and hides sensitive URL parameters', () => {
    expect(toolIdentity('webfetch')).toEqual({ label: 'Webseite abrufen', icon: 'language' })
    expect(toolSummary({
      name: 'webfetch',
      arguments: { url: 'https://user:secret@example.com/private?token=secret' }
    })).toBe('https://example.com/private?…')
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
    expect(formatToolArguments({ content: 'vertraulich' }, 'generate_qr_code'))
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
      items: [{ id: 'memory-1', content: 'Sichtbarer Inhalt', category: 'fact', pinned: true }]
    }), 'memory_retrieve')

    expect(parsed.text).toContain('Sichtbarer Inhalt')
    expect(parsed.text).toContain('private')
    expect(parsed.text).toContain('pinned')
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
    expect(formatToolArguments(tool.arguments, tool.name)).not.toContain(content)
    expect(formatToolArguments(tool.arguments, tool.name)).toContain('context')
  })

  it('shows persisted legacy memory previews', () => {
    const content = 'Kurzes vollständiges Memory'
    expect(memoryWriteContent({
      name: 'memory_write',
      arguments: { content: { preview: content, characters: content.length } }
    })).toBe(content)
  })

  it('redacts sensitive arguments and bounds their output', () => {
    const formatted = formatToolArguments({ path: 'plan.md', content: 'vertraulich', token: 'secret' })
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
