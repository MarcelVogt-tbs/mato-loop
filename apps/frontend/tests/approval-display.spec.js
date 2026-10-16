import { describe, expect, it } from 'vitest'

import {
  approvalResource,
  memoryApprovalDisplay,
  permissionGrantDisplay,
  shellClassificationDisplay,
  technicalDetailsDisplay
} from '../src/utils/approval-display'

describe('approval display', () => {
  it('accepts and marks a removal approval without an after state as destructive', () => {
    const display = memoryApprovalDisplay({
      requestType: 'memory_change',
      actionKind: 'memory_remove',
      availableDecisions: ['approve', 'deny'],
      payload: {
        targetMemories: [{ id: 'memory-1', content: 'Veraltete Erinnerung' }]
      }
    })

    expect(display.invalid).toBe(false)
    expect(display.title).toBe('Erinnerung dauerhaft entfernen')
    expect(display.summary).toBe('Dreaming möchte diese Erinnerung unwiderruflich löschen.')
    expect(display.targetLabel).toBe('Zu entfernende Erinnerung')
    expect(display.icon).toBe('delete_forever')
    expect(display.destructive).toBe(true)
  })

  it('still requires an after state for update approvals', () => {
    expect(memoryApprovalDisplay({
      actionKind: 'memory_update',
      availableDecisions: ['approve', 'deny'],
      payload: { targetMemories: [{ id: 'memory-1' }] }
    }).invalid).toBe(true)
  })

  it('keeps legacy pin approvals displayable without an after state', () => {
    const display = memoryApprovalDisplay({
      actionKind: 'memory_pin',
      availableDecisions: ['approve', 'deny'],
      payload: { requestedPin: true, targetMemories: [{ id: 'memory-1' }] }
    })

    expect(display.invalid).toBe(false)
    expect(display.icon).toBe('push_pin')
  })

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
      resourceType: 'session',
      resourceScope: 'session-2',
      sessionId: 'session-1',
      resourceDisplay: 'Projekt Aurora',
      details: {}
    })).toEqual({ label: 'Session', value: '„Projekt Aurora“' })
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
    })).toEqual({ label: 'Ordner und Unterordner', value: '/home/user/projekt' })
    expect(approvalResource({
      resourceType: 'execution_agent',
      resourceDisplay: 'Notebook'
    })).toEqual({ label: 'Computer', value: 'Notebook' })
    expect(approvalResource({
      resourceType: 'execution_workspace',
      resourceScope: '{opaque}',
      resourceDisplay: '/home/user/projekt'
    })).toEqual({ label: 'Aktueller Ordner', value: '/home/user/projekt' })
    expect(approvalResource({
      resourceType: 'root_path',
      resourceScope: '{opaque}',
      resourceDisplay: 'session:plan.md'
    })).toEqual({ label: 'Datei oder Ordner', value: 'session:plan.md' })

    for (const [capability, label, icon] of [
      ['execution.directory', 'Im Ordnerbaum navigieren', 'drive_file_move'],
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

  it('does not describe legacy areas as inherited file and shell access', () => {
    expect(permissionGrantDisplay({
      capability: 'resource.read',
      resourceType: 'execution_directory',
      resourceDisplay: '/opt'
    })).toEqual({
      label: 'Frühere Bereichsfreigabe',
      value: '/opt',
      icon: 'visibility'
    })
    expect(permissionGrantDisplay({
      capability: 'resource.write',
      resourceType: 'integration_resource',
      resourceDisplay: 'Kalender „Privat“'
    })).toEqual({
      label: 'Alle passenden schreibenden Aktionen',
      value: 'Kalender „Privat“',
      icon: 'edit'
    })
    expect(permissionGrantDisplay({
      capability: 'resource.read',
      resourceType: 'root_path',
      resourceDisplay: 'Sitzungsdateien'
    })).toEqual({
      label: 'Frühere Bereichsfreigabe',
      value: 'Sitzungsdateien',
      icon: 'visibility'
    })
  })

  it.each(['execution_path', 'session_path', 'administrator_path'])('shows the exact %s resource rather than its parent directory', (resourceType) => {
    expect(permissionGrantDisplay({
      capability: 'resource.write', resourceType, resourceDisplay: '/opt/config.ini'
    })).toEqual({
      label: 'Exakter Pfad · Schreiben', value: '/opt/config.ini',
      icon: resourceType === 'administrator_path' ? 'admin_panel_settings' : 'edit'
    })
  })

  it.each([
    ['read', 'Lesen'], ['read_sensitive', 'Vertrauliche Daten lesen'],
    ['write', 'Schreiben'], ['delete', 'Löschen']
  ])('shows the %s category and its recursive resource', (category, label) => {
    expect(permissionGrantDisplay({
      capability: `category.${category}`, resourceType: 'execution_path',
      resourceDisplay: '/srv/project', recursive: true
    })).toMatchObject({ label, value: '/srv/project/**' })
  })

  it('labels shell-action grants independently of file access and uses their bound directory', () => {
    expect(permissionGrantDisplay({
      capability: 'resource.read', resourceType: 'shell_action',
      currentDirectory: '/opt/project', command: 'git status'
    })).toEqual({ label: 'Exakter Shell-Befehl · Lesen', value: '/opt/project', icon: 'terminal' })
  })

  it('shows mixed shell categories instead of hiding deletion behind write', () => {
    expect(shellClassificationDisplay({ payload: { shellCategories: ['write', 'delete'] } }))
      .toEqual({ color: 'negative', label: 'Einstufung: Schreiben, Löschen', incomplete: false })
  })

  it('labels computer-bound shell grants separately from file grants', () => {
    expect(permissionGrantDisplay({ capability: 'category.delete', resourceType: 'shell_computer', resourceDisplay: 'Shell' }))
      .toMatchObject({ label: 'Shell · Löschen' })
  })

  it.each([
    ['shell.read', 'Shell-Befehle ohne erkannte Schreibaktionen'],
    ['shell.write', 'Als schreibend eingestufte Shell-Befehle']
  ])('shows %s grant classification and token prefixes', (capability, label) => {
    expect(permissionGrantDisplay({
      capability,
      resourceType: 'shell_command',
      resourceDisplay: '/home/user/project',
      shellPrefixes: [['ls', '-l'], ['cat']]
    })).toEqual({
      label,
      value: '/home/user/project · ls -l · cat',
      icon: 'terminal'
    })
  })

  it.each([
    ['read', 'primary', 'Keine Schreibaktion erkannt'],
    ['write', 'warning', 'Als schreibend eingestuft']
  ])('keeps %s classification independent of analysis completeness', (classification, color, label) => {
    for (const complete of [true, false, undefined, null]) {
      const approval = Object.freeze({
        title: 'Gespeicherter historischer Titel',
        riskLevel: classification === 'read' ? 'low' : 'high',
        payload: Object.freeze({
          shellClassification: classification,
          ...(complete === undefined ? {} : { shellAnalysisComplete: complete })
        })
      })
      expect(shellClassificationDisplay(approval)).toEqual({
        color, label, incomplete: complete === false
      })
    }
  })

  it('does not infer shell classification for legacy, unknown or non-shell payloads', () => {
    for (const approval of [
      undefined,
      {},
      { capability: 'shell.read', payload: { shellAnalysisComplete: false } },
      { payload: { shellClassification: 'unknown', shellAnalysisComplete: false } },
      { capability: 'filesystem.write' }
    ]) {
      expect(shellClassificationDisplay(approval)).toBeNull()
    }
  })

  it('shows cleaned URL approvals and readable session grants', () => {
    const approval = {
      resourceType: 'url',
      resourceScope: 'https://user:secret@example.com/report?token=secret'
    }
    expect(approvalResource(approval)).toEqual({
      label: 'Webadresse',
      value: 'https://example.com/report?token=secret'
    })
    expect(permissionGrantDisplay(approval)).toEqual({
      label: 'Webseite abrufen · Exakte URL',
      value: 'https://example.com/report?token=secret',
      icon: 'language'
    })

    expect(permissionGrantDisplay({
      resourceType: 'url',
      resourceScope: 'a'.repeat(64),
      resourceDisplay: 'https://example.com/report?REDACTED'
    })).toEqual({
      label: 'Webseite abrufen · Exakte URL',
      value: 'https://example.com/report?REDACTED · aaaaaaaa',
      icon: 'language'
    })
  })

  it('preserves URL query values in technical details', () => {
    const formatted = technicalDetailsDisplay({ url: 'https://example.com/page?token=secret' }).text
    expect(formatted).toContain('https://example.com/page?token=secret')
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
      ['recipes.write', 'Rezepte ändern', 'edit_note'],
      ['recipes.delete', 'Rezepte löschen', 'delete'],
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

  it('uses generic labels for unrecognized historical grants', () => {
    expect(permissionGrantDisplay({
      capability: 'retired.read',
      resourceType: 'integration_operation',
      resourceDisplay: 'Gespeicherte Ressource'
    })).toEqual({ label: 'Tool-Zugriff', value: 'Gespeicherte Ressource', icon: 'verified_user' })
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

  it('labels personal profile approvals and grants', () => {
    const approval = {
      capability: 'settings.user_profile.update',
      resourceType: 'user_profile',
      resourceDisplay: 'Eigenes persönliches Profil'
    }

    expect(approvalResource(approval)).toEqual({
      label: 'Persönliches Profil',
      value: 'Eigenes persönliches Profil'
    })
    expect(permissionGrantDisplay(approval)).toEqual({
      label: 'Persönliches Profil ändern',
      value: 'Eigenes persönliches Profil',
      icon: 'manage_accounts'
    })
  })

  it('labels automation resources and grants', () => {
    expect(approvalResource({ resourceType: 'automation_collection' })).toEqual({
      label: 'Automationen', value: 'Eigene Automationen'
    })
    expect(approvalResource({ resourceType: 'automation', resourceDisplay: 'Morgenbericht (Revision 2)' })).toEqual({
      label: 'Automation', value: 'Morgenbericht (Revision 2)'
    })
    expect(permissionGrantDisplay({
      capability: 'automation.run', resourceType: 'automation', resourceDisplay: 'Morgenbericht'
    })).toEqual({ label: 'Automation starten', value: 'Morgenbericht', icon: 'play_arrow' })
  })

  it('redacts sensitive and bounds large technical details', () => {
    const formatted = technicalDetailsDisplay({
      path: 'Notizen/plan.md',
      content: 'vertraulicher Inhalt',
      nested: { token: 'secret', values: Array.from({ length: 40 }, (_, index) => index) }
    }).text

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
