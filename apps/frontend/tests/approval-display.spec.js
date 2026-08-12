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

  it('labels skill write grants', () => {
    expect(permissionGrantDisplay({
      capability: 'skills.write',
      resourceType: 'skill_collection'
    })).toEqual({
      label: 'Skills erstellen',
      value: 'Persönliche Skills',
      icon: 'auto_awesome'
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
