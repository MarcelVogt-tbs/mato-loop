import { describe, expect, it } from 'vitest'

import {
  approvalResource,
  approvalActionDisplay,
  approvalGrantOptions,
  memoryApprovalDisplay,
  permissionGrantDisplay,
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
      format: 'code',
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
    })).toEqual({ label: 'Ordner und Unterordner', value: '/home/user/projekt', format: 'code' })
    expect(approvalResource({
      resourceType: 'execution_agent',
      resourceDisplay: 'Notebook'
    })).toEqual({ label: 'Computer', value: 'Notebook' })
    expect(approvalResource({
      resourceType: 'execution_workspace',
      resourceScope: '{opaque}',
      resourceDisplay: '/home/user/projekt'
    })).toEqual({ label: 'Aktueller Ordner', value: '/home/user/projekt', format: 'code' })
    expect(approvalResource({
      resourceType: 'root_path',
      resourceScope: '{opaque}',
      resourceDisplay: 'session:plan.md'
    })).toEqual({ label: 'Datei oder Ordner', value: 'session:plan.md', format: 'code' })

    expect(permissionGrantDisplay({
      capability: 'execution.directory',
      resourceType: 'execution_directory',
      resourceDisplay: '/home/user/projekt'
    })).toEqual({
      label: 'Im Ordnerbaum navigieren', value: '/home/user/projekt', icon: 'drive_file_move'
    })
  })

  it.each([
    ['read', 'Lesen'], ['read_sensitive', 'Vertrauliche Daten lesen'],
    ['write', 'Lesen und Schreiben'], ['delete', 'Löschen']
  ])('shows the %s category and its recursive resource', (category, label) => {
    expect(permissionGrantDisplay({
      capability: `category.${category}`, resourceType: 'execution_path',
      resourceDisplay: '/srv/project', recursive: true
    })).toMatchObject({ label, value: '/srv/project/**' })
  })

  it('labels shell-action grants independently of file access and uses their bound directory', () => {
    expect(permissionGrantDisplay({
      capability: 'category.read', resourceType: 'shell_action',
      currentDirectory: '/opt/project', command: 'git status'
    })).toEqual({ label: 'Exakter Shell-Befehl · Lesen', value: '/opt/project', icon: 'visibility' })
  })

  it('shows mixed shell categories instead of hiding deletion behind write', () => {
    expect(approvalActionDisplay({ payload: { shellCategories: ['write', 'delete'] } }))
      .toMatchObject({ chips: [{ label: 'Lesen' }, { label: 'Schreiben' }, { label: 'Löschen' }], incomplete: false })
  })

  it('does not advertise additional inspected categories as part of the requested grant', () => {
    expect(approvalActionDisplay({ payload: {
      shellCategories: ['write', 'delete'], sessionGrants: [{ category: 'write' }]
    } })?.chips.map((chip) => chip.label)).toEqual(['Lesen', 'Schreiben'])
  })

  it('labels computer-bound shell grants separately from file grants', () => {
    expect(permissionGrantDisplay({ capability: 'category.delete', resourceType: 'shell_computer', resourceDisplay: 'Shell' }))
      .toMatchObject({ label: 'Shell · Löschen' })
  })

  it.each([
    ['read', ['Lesen']],
    ['write', ['Lesen', 'Schreiben']]
  ])('keeps %s classification independent of analysis completeness', (classification, labels) => {
    for (const complete of [true, false, undefined, null]) {
      const approval = Object.freeze({
        title: 'Gespeicherter historischer Titel',
        riskLevel: classification === 'read' ? 'low' : 'high',
        payload: Object.freeze({
          shellClassification: classification,
          ...(complete === undefined ? {} : { shellAnalysisComplete: complete })
        })
      })
      expect(approvalActionDisplay(approval)).toMatchObject({
        chips: labels.map((label) => ({ label })), incomplete: complete === false
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
      expect(approvalActionDisplay(approval)).toBeNull()
    }
  })

  it.each([
    ['read', ['Lesen']], ['read_sensitive', ['Vertrauliche Daten lesen']],
    ['write', ['Lesen', 'Schreiben']], ['delete', ['Löschen']], ['navigate', ['Navigieren']]
  ])('uses the declared %s category for any resource type', (category, labels) => {
    expect(approvalActionDisplay({ payload: { sessionGrants: [{ category }] } })?.chips.map((chip) => chip.label)).toEqual(labels)
  })

  it('retains the full description for grant options without compact scope data', () => {
    expect(approvalGrantOptions({ payload: { grantOptions: [{
      id: 'requested', label: 'Angefragter Bereich', display: ['Lesen: unbekannte Ressource']
    }] } })[0].label).toBe('Lesen: unbekannte Ressource')
  })

  it('omits duplicate administrator rights from stored shell scope labels', () => {
    expect(approvalGrantOptions({
      resourceType: 'execution_administrator', actionKind: 'shell_run',
      payload: { grantOptions: [{
        id: 'requested', scopeDisplay: ['Diesen Befehl mit Administratorrechten'],
        scopeHint: 'Nur im angezeigten Ausführungskontext.', caption: 'Gilt bis zum Widerruf.'
      }] }
    })[0]).toEqual({ id: 'requested', label: 'Diesen Befehl' })
  })

  it('shows cleaned URL approvals', () => {
    const approval = {
      resourceType: 'url',
      resourceScope: 'https://user:secret@example.com/report?token=secret'
    }
    expect(approvalResource(approval)).toEqual({
      label: 'Webadresse',
      value: 'https://example.com/report?token=secret'
    })
  })

  it('preserves URL query values in technical details', () => {
    const formatted = technicalDetailsDisplay({ url: 'https://example.com/page?token=secret' }).text
    expect(formatted).toContain('https://example.com/page?token=secret')
  })

  it('describes integration operations and sensitive session grants', () => {
    expect(approvalResource({
      resourceType: 'integration_operation',
      resourceScope: '{opaque}',
      resourceDisplay: 'calendar:work'
    })).toEqual({ label: 'Integrationsressource', value: 'calendar:work' })
    expect(permissionGrantDisplay({
      capability: 'category.read_sensitive',
      resourceType: 'session',
      resourceScope: 'session-1',
      sessionId: 'session-1'
    })).toEqual({
      label: 'Vertrauliche Daten lesen',
      value: 'Aktuelle Session',
      icon: 'key'
    })
  })

  it('labels integration grants by category and bound resource', () => {
    expect(permissionGrantDisplay({
      capability: 'category.write', resourceType: 'integration_resource', resourceDisplay: 'Aufgabenliste Arbeit'
    })).toEqual({ label: 'Lesen und Schreiben', value: 'Aufgabenliste Arbeit', icon: 'edit' })
  })

  it('uses generic labels for unknown grants', () => {
    expect(permissionGrantDisplay({
      capability: 'retired.read',
      resourceType: 'integration_operation',
      resourceDisplay: 'Gespeicherte Ressource'
    })).toEqual({ label: 'Tool-Zugriff', value: 'Gespeicherte Ressource', icon: 'verified_user' })
  })

  it('labels reviewed run actions', () => {
    expect(permissionGrantDisplay({
      capability: 'skills.write', resourceType: 'run_action', resourceDisplay: 'Songfinder'
    })).toEqual({
      label: 'Bestätigte Aktion', value: 'Songfinder', icon: 'verified_user'
    })
  })

  it('labels personal profile approvals', () => {
    const approval = {
      capability: 'settings.user_profile.update',
      resourceType: 'user_profile',
      resourceDisplay: 'Eigenes persönliches Profil'
    }

    expect(approvalResource(approval)).toEqual({
      label: 'Persönliches Profil',
      value: 'Eigenes persönliches Profil'
    })
  })

  it('labels automation resources', () => {
    expect(approvalResource({ resourceType: 'automation_collection' })).toEqual({
      label: 'Automationen', value: 'Eigene Automationen'
    })
    expect(approvalResource({ resourceType: 'automation', resourceDisplay: 'Morgenbericht (Revision 2)' })).toEqual({
      label: 'Automation', value: 'Morgenbericht (Revision 2)'
    })
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
