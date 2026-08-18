import { describe, expect, it } from 'vitest'

import {
  approvalResource,
  formatTechnicalDetails,
  permissionGrantDisplay,
  technicalDetailsDisplay
} from '../src/utils/approval-display'

describe('approval display', () => {
  it('keeps file scopes prominent and unchanged', () => {
    expect(approvalResource({ resourceType: 'path', resourceScope: 'Projekte/demo.txt' })).toEqual({
      label: 'Datei oder Ordner',
      value: 'Projekte/demo.txt'
    })
  })

  it('describes session resources without exposing the current session id', () => {
    expect(approvalResource({
      resourceType: 'session',
      resourceScope: 'session-1',
      sessionId: 'session-1',
      details: {}
    })).toEqual({ label: 'Session', value: 'Aktuelle Session' })
    expect(approvalResource({
      resourceType: 'session_collection',
      resourceScope: '*',
      details: {}
    })).toEqual({ label: 'Session-Sammlung', value: 'Alle Sessions' })
    expect(approvalResource({
      resourceType: 'skill_collection',
      resourceDisplay: 'API-Recherche'
    })).toEqual({ label: 'Skill-Sammlung', value: '„API-Recherche“' })
  })

  it('describes execution resources and capabilities', () => {
    expect(approvalResource({
      resourceType: 'execution_directory',
      resourceScope: 'opaque',
      resourceDisplay: '/home/user/projekt'
    })).toEqual({ label: 'Aktueller Ordner', value: '/home/user/projekt' })
    expect(approvalResource({
      resourceType: 'execution_agent',
      resourceDisplay: 'Notebook'
    })).toEqual({ label: 'Computer', value: 'Notebook' })
    expect(approvalResource({
      resourceType: 'execution_workspace',
      resourceScope: '{opaque}',
      resourceDisplay: '/home/user/projekt'
    })).toEqual({ label: 'Arbeitsverzeichnis', value: '/home/user/projekt' })
    expect(approvalResource({
      resourceType: 'root_path',
      resourceScope: '{opaque}',
      resourceDisplay: 'session:plan.md'
    })).toEqual({ label: 'Datei oder Ordner', value: 'session:plan.md' })

    for (const [capability, label, icon] of [
      ['execution.directory', 'Aktuellen Ordner wechseln', 'drive_file_move'],
      ['system.info', 'Systeminformationen lesen', 'computer'],
      ['process.execute', 'Befehl ausführen', 'terminal']
    ]) {
      expect(permissionGrantDisplay({
        capability,
        resourceType: 'execution_agent',
        resourceDisplay: 'Notebook'
      })).toEqual({ label, value: 'Notebook', icon })
    }
  })

  it('shows cleaned URL approvals and readable session grants', () => {
    const approval = {
      resourceType: 'url',
      resourceScope: 'https://user:secret@example.com/report?token=secret'
    }
    expect(approvalResource(approval)).toEqual({
      label: 'Webadresse',
      value: 'https://example.com/report?…'
    })
    expect(permissionGrantDisplay(approval)).toEqual({
      label: 'Webseite abrufen · Exakte URL',
      value: 'https://example.com/report?…',
      icon: 'language'
    })

    expect(permissionGrantDisplay({
      resourceType: 'url',
      resourceScope: 'a'.repeat(64),
      resourceDisplay: 'https://example.com/report?REDACTED'
    })).toEqual({
      label: 'Webseite abrufen · Exakte URL',
      value: 'https://example.com/report?… · aaaaaaaa',
      icon: 'language'
    })
  })

  it('redacts URL query values from technical details', () => {
    const formatted = formatTechnicalDetails({ url: 'https://example.com/page?token=secret' })
    expect(formatted).toContain('https://example.com/page?…')
    expect(formatted).not.toContain('secret')
  })

  it('describes integration operations and private-data grants', () => {
    expect(approvalResource({
      resourceType: 'integration_operation',
      resourceScope: '{opaque}',
      resourceDisplay: 'calendar:work'
    })).toEqual({ label: 'Integrationsressource', value: 'calendar:work' })
    expect(permissionGrantDisplay({
      capability: 'integration.private_data.release',
      resourceType: 'session',
      resourceScope: 'session-1',
      sessionId: 'session-1'
    })).toEqual({
      label: 'Private Integrationsdaten freigeben',
      value: 'Aktuelle Session',
      icon: 'cloud_upload'
    })
  })

  it('labels task grants', () => {
    for (const [capability, label, icon] of [
      ['tasks.read', 'Aufgaben lesen', 'checklist'],
      ['tasks.write', 'Aufgaben ändern', 'edit_note'],
      ['tasks.delete', 'Aufgaben löschen', 'delete_outline']
    ]) {
      expect(permissionGrantDisplay({
        capability,
        resourceType: 'integration_operation',
        resourceDisplay: 'Aufgabenliste Arbeit'
      })).toEqual({ label, value: 'Aufgabenliste Arbeit', icon })
    }
  })

  it('labels knowledge grants', () => {
    for (const [capability, label, icon] of [
      ['knowledge.read', 'Wissensdatenbank lesen', 'menu_book'],
      ['knowledge.write', 'Wissensdatenbank ändern', 'edit_note'],
      ['knowledge.delete', 'Wissenseintrag löschen', 'delete_sweep']
    ]) {
      expect(permissionGrantDisplay({
        capability,
        resourceType: 'integration_operation',
        resourceDisplay: '„Handbuch“ · Wissensdatenbank „Wiki“'
      })).toEqual({ label, value: '„Handbuch“ · Wissensdatenbank „Wiki“', icon })
    }
  })

  it('labels recipe and meal-plan grants', () => {
    for (const [capability, label, icon] of [
      ['mealie.accounts.read', 'Mealie-Konten auflisten', 'restaurant'],
      ['recipes.read', 'Rezepte lesen', 'menu_book'],
      ['meal_plan.read', 'Essensplan lesen', 'calendar_month'],
      ['meal_plan.write', 'Essensplan ändern', 'edit_calendar'],
      ['meal_plan.delete', 'Essensplan-Eintrag löschen', 'event_busy']
    ]) {
      expect(permissionGrantDisplay({
        capability,
        resourceType: 'integration_operation',
        resourceDisplay: 'Mealie-Konto „Familienküche“'
      })).toEqual({ label, value: 'Mealie-Konto „Familienküche“', icon })
    }
  })

  it('labels Git grants', () => {
    for (const [capability, label, icon] of [
      ['git.read', 'Git-Repository lesen', 'account_tree'],
      ['git.stage', 'Git-Änderungen vormerken', 'add_task'],
      ['git.clone', 'Git-Repository klonen', 'account_tree'],
      ['git.commit', 'Git-Commit erstellen', 'commit']
    ]) {
      expect(permissionGrantDisplay({
        capability,
        resourceType: 'integration_operation',
        resourceDisplay: 'Repository „MatoLoop“'
      })).toEqual({ label, value: 'Repository „MatoLoop“', icon })
    }
  })

  it('labels skill write grants', () => {
    expect(permissionGrantDisplay({
      capability: 'skills.write',
      resourceType: 'skill_collection'
    })).toEqual({
      label: 'Skills erstellen',
      value: 'Persönliche Skills',
      icon: 'auto_awesome'
    })
    expect(permissionGrantDisplay({
      capability: 'skills.write',
      resourceType: 'skill',
      resourceDisplay: 'Songfinder'
    })).toEqual({
      label: 'Skill bearbeiten',
      value: '„Songfinder“',
      icon: 'edit_note'
    })
  })

  it('redacts sensitive and bounds large technical details', () => {
    const formatted = formatTechnicalDetails({
      path: 'Notizen/plan.md',
      content: 'vertraulicher Inhalt',
      nested: { token: 'secret', values: Array.from({ length: 40 }, (_, index) => index) }
    })

    expect(formatted).toContain('Notizen/plan.md')
    expect(formatted).not.toContain('vertraulicher Inhalt')
    expect(formatted).not.toContain('secret')
    expect(formatted).toContain('[Ausgeblendet]')
    expect(formatted).toContain('20 weitere Einträge')
    expect(formatted.length).toBeLessThanOrEqual(5040)
  })

  it('provides complete technical details without exposing sensitive values', () => {
    const prompt = 'Ausführliche Bildbeschreibung. '.repeat(40)
    const display = technicalDetailsDisplay({ prompt, token: 'secret' })

    expect(display.truncated).toBe(true)
    expect(display.text).toContain('[gekürzt]')
    expect(JSON.parse(display.fullText)).toEqual({ prompt, token: '[Ausgeblendet]' })
    expect(display.fullText).not.toContain('secret')
  })
})
