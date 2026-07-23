import { describe, expect, it } from 'vitest'

import {
  formatToolArguments,
  parseToolResult,
  toolIdentity,
  toolStatus,
  toolSummary
} from '../src/utils/tool-display'

describe('tool display', () => {
  it('uses readable labels and concise resource summaries', () => {
    expect(toolIdentity('read_file')).toEqual({ label: 'Datei lesen', icon: 'description' })
    expect(toolSummary({ name: 'read_file', arguments: { path: 'Projekte/plan.md' } })).toBe('Projekte/plan.md')
  })

  it('identifies webfetch exactly and hides sensitive URL parameters', () => {
    expect(toolIdentity('webfetch')).toEqual({ label: 'Webseite abrufen', icon: 'language' })
    expect(toolSummary({
      name: 'webfetch',
      arguments: { url: 'https://user:secret@example.com/private?token=secret' }
    })).toBe('https://example.com/private?…')
  })

  it('renders memory retrieval with its visible scope and contents', () => {
    expect(toolIdentity('memory_retrieve')).toEqual({ label: 'Memory abrufen', icon: 'psychology' })
    expect(toolSummary({
      name: 'memory_retrieve',
      arguments: { mode: 'browse', cursor_id: 'memory-0' }
    })).toBe('Memory-Übersicht fortsetzen')

    const parsed = parseToolResult(JSON.stringify({
      mode: 'browse',
      scope: { readableBy: 'private', retrievalLevel: 'all' },
      total: 1,
      nextCursor: { cursor_updated_at: '2026-07-23T12:00:00Z', cursor_id: 'memory-1' },
      items: [{ id: 'memory-1', content: 'Sichtbarer Inhalt', category: 'fact' }]
    }), 'memory_retrieve')

    expect(parsed.text).toContain('Sichtbarer Inhalt')
    expect(parsed.text).toContain('private')
    expect(parsed.text).toContain('cursor_updated_at')
  })

  it('redacts sensitive arguments and bounds their output', () => {
    const formatted = formatToolArguments({ path: 'plan.md', content: 'vertraulich', token: 'secret' })
    expect(formatted).toContain('plan.md')
    expect(formatted).not.toContain('vertraulich')
    expect(formatted).not.toContain('secret')
  })

  it('translates structured tool errors and derives their status', () => {
    const result = { content: JSON.stringify({ error: { code: 'tool.invalidArguments' } }) }
    expect(parseToolResult(result.content).errorMessage).toBe('Das Modell hat ungültige Tool-Argumente erzeugt.')
    expect(toolStatus({ result })).toMatchObject({ name: 'failed', color: 'negative' })
  })

  it('distinguishes running, approval and successful calls', () => {
    expect(toolStatus({ active: true }).name).toBe('running')
    expect(toolStatus({ approval: { id: 'approval-1' } }).name).toBe('approval')
    expect(toolStatus({ result: { content: '{"written":"plan.md"}' } }).name).toBe('completed')
  })
})
