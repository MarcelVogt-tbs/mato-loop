import { describe, expect, it } from 'vitest'

import { apiErrorMessage, errorDescriptor } from '../src/utils/api-errors'

describe('API error messages', () => {
  it('translates stable domain error codes', () => {
    expect(apiErrorMessage({ response: { data: { error: { code: 'auth.invalidCredentials' } } } })).toBe(
      'Benutzername oder Passwort ist ungültig.'
    )
    expect(apiErrorMessage({ error: { code: 'integration.searchUnavailable' } })).toBe(
      'Richte zuerst eine aktive Websuche mit Standardanbieter ein.'
    )
    expect(apiErrorMessage({ error: { code: 'provider.discoveryUnsupported' } })).toBe(
      'Dieser Provider unterstützt keine automatische Modellerkennung.'
    )
    expect(apiErrorMessage({ error: { code: 'oauth.loginExpired' } })).toBe(
      'Der ChatGPT-Einmalcode ist abgelaufen. Bitte starte die Anmeldung erneut.'
    )
    expect(apiErrorMessage({ error: { code: 'workspace.invalidEncoding' } })).toBe(
      'Die Datei kann nur als UTF-8-Text gelesen werden.'
    )
    expect(apiErrorMessage({ error: { code: 'webfetch.addressBlocked' } })).toBe(
      'Diese Netzwerkadresse ist aus Sicherheitsgründen gesperrt.'
    )
    expect(apiErrorMessage({ error: { code: 'attachment.tooLarge' } })).toBe(
      'Der Bildanhang ist zu groß.'
    )
    expect(apiErrorMessage({ error: { code: 'agent.requiredWebSearchMissing' } })).toBe(
      'Das Modell hat die verpflichtende Websuche auch nach mehreren Versuchen nicht ausgeführt.'
    )
    expect(apiErrorMessage({ error: { code: 'memory.dreamingModelUnavailable' } })).toBe(
      'Für Dreaming ist kein verfügbares Modell eingerichtet.'
    )
    expect(apiErrorMessage({ error: { code: 'memory.disabled' } })).toBe(
      'Memory ist deaktiviert.'
    )
    expect(apiErrorMessage({ error: { code: 'memory.duplicate' } })).toBe(
      'Diese Erinnerung ist bereits vorhanden.'
    )
    expect(apiErrorMessage({ error: { code: 'privacy.pseudonymizationUnavailable' } })).toBe(
      'Die lokale Pseudonymisierung ist derzeit nicht verfügbar.'
    )
  })

  it('formats validation parameters without backend text', () => {
    const source = {
      response: {
        data: {
          error: {
            code: 'validation.tooShort',
            field: 'password',
            params: { minLength: 10 }
          }
        }
      }
    }

    expect(apiErrorMessage(source)).toBe('Das Passwort muss mindestens 10 Zeichen lang sein.')
  })

  it('never renders legacy validation payloads', () => {
    const source = {
      response: {
        status: 422,
        data: {
          detail: [{ type: 'string_too_short', loc: ['body', 'password'], input: 'secret' }]
        }
      }
    }

    expect(errorDescriptor(source)).toBeNull()
    expect(apiErrorMessage(source)).toBe('Bitte überprüfe deine Eingaben.')
  })

  it('uses safe fallbacks for unknown and network errors', () => {
    expect(apiErrorMessage({ error: { code: 'future.unknown' } }, 'Aktion fehlgeschlagen')).toBe(
      'Aktion fehlgeschlagen'
    )
    expect(apiErrorMessage({ request: {} })).toBe('MatoLoop ist nicht erreichbar.')
  })
})
