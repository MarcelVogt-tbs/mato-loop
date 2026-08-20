import { describe, expect, it } from 'vitest'

import { apiErrorMessage, decodeBlobApiError, errorDescriptor } from '../src/utils/api-errors'

describe('API error messages', () => {
  it('translates stable domain error codes', () => {
    expect(apiErrorMessage({ response: { data: { error: { code: 'auth.invalidCredentials' } } } })).toBe(
      'Benutzername oder Passwort ist ungültig.'
    )
    expect(apiErrorMessage({ error: { code: 'integration.searchUnavailable' } })).toBe(
      'Richte zuerst eine aktive Websuche mit Standardanbieter ein.'
    )
    expect(apiErrorMessage({ error: { code: 'integration.remoteChanged' } })).toBe(
      'Der entfernte Eintrag wurde zwischenzeitlich geändert. Lade ihn erneut.'
    )
    expect(apiErrorMessage({ error: { code: 'integration.permissionDenied' } })).toBe(
      'Der verbundene Dienst erlaubt diese Aktion nicht.'
    )
    expect(apiErrorMessage({ error: { code: 'integration.adapterDisabled' } })).toBe(
      'Dieser Integrationstyp ist derzeit nicht verfügbar.'
    )
    expect(apiErrorMessage({ error: { code: 'provider.discoveryUnsupported' } })).toBe(
      'Dieser Provider unterstützt keine automatische Modellerkennung.'
    )
    expect(apiErrorMessage({ error: { code: 'provider.modelCapabilityUnavailable' } })).toBe(
      'Das ausgewählte Modell unterstützt die benötigte Fähigkeit nicht.'
    )
    expect(apiErrorMessage({ error: { code: 'provider.imageLimitExceeded', params: { maxImages: 4 } } })).toBe(
      'Eine Modellanfrage kann höchstens 4 Bilder enthalten.'
    )
    expect(apiErrorMessage({ error: { code: 'oauth.loginExpired' } })).toBe(
      'Der ChatGPT-Einmalcode ist abgelaufen. Bitte starte die Anmeldung erneut.'
    )
    expect(apiErrorMessage({ error: { code: 'openscad.renderFailed' } })).toBe(
      'Das OpenSCAD-Modell konnte nicht gerendert werden.'
    )
    expect(apiErrorMessage({ error: { code: 'project.notFound' } })).toBe(
      'Das Projekt wurde nicht gefunden.'
    )
    expect(apiErrorMessage({ error: { code: 'project.notEmpty' } })).toBe(
      'Das Projekt enthält noch Sessions.'
    )
    for (const code of [
      'cache.exportFailed',
      'cache.invalidContent',
      'cache.invalidDescriptor',
      'cache.invalidPath',
      'cache.notFound',
      'openscad.alreadyConfigured',
      'openscad.invalidArtifact',
      'openscad.invalidArchive',
      'openscad.invalidExecutable',
      'openscad.notConfigured',
      'openscad.notFound',
      'openscad.previewFailed',
      'openscad.previewUnavailable',
      'openscad.projectTooLarge',
      'openscad.testFailed',
      'openscad.unavailable',
      'openscad.unsafeProject'
    ]) {
      expect(apiErrorMessage({ error: { code } })).not.toBe('Die Aktion ist fehlgeschlagen.')
    }
    expect(apiErrorMessage({ error: { code: 'workspace.invalidEncoding' } })).toBe(
      'Die Datei kann nur als UTF-8-Text gelesen werden.'
    )
    expect(apiErrorMessage({ error: { code: 'file.workspaceNotSelected' } })).toBe(
      'Für diese Session ist kein Arbeitsverzeichnis ausgewählt.'
    )
    expect(apiErrorMessage({ error: { code: 'file.pathAlreadyExists' } })).toBe(
      'Der exakte Zielpfad existiert bereits. Dateien werden nicht überschrieben.'
    )
    expect(apiErrorMessage({ error: { code: 'file.changed' } })).toBe(
      'Die Datei wurde seit dem Öffnen geändert. Öffne sie erneut und übernimm deine Änderungen in die aktuelle Version.'
    )
    expect(apiErrorMessage({ error: { code: 'file.sessionQuotaExceeded' } })).toBe(
      'Das Speicherlimit für Dateien dieser Session ist erreicht.'
    )
    expect(apiErrorMessage({ error: { code: 'file.operationTooLarge' } })).toBe(
      'Die Quelle ist für eine einzelne Dateioperation zu groß oder enthält zu viele Einträge.'
    )
    expect(apiErrorMessage({ error: { code: 'session.storageUnavailable' } })).toBe(
      'Der interne Dateispeicher dieser Session ist derzeit nicht verfügbar.'
    )
    expect(apiErrorMessage({ error: { code: 'session.messageNotFound' } })).toBe(
      'Die Chatnachricht wurde nicht gefunden.'
    )
    expect(apiErrorMessage({ error: { code: 'session.retryUnavailable' } })).toBe(
      'Diese fehlgeschlagene Antwort kann nicht mehr erneut versucht werden.'
    )
    expect(apiErrorMessage({ error: { code: 'webfetch.addressBlocked' } })).toBe(
      'Diese Netzwerkadresse ist aus Sicherheitsgründen gesperrt.'
    )
    expect(apiErrorMessage({ error: { code: 'webfetch.tlsFailed' } })).toBe(
      'Das TLS-Zertifikat der Webadresse konnte nicht überprüft werden.'
    )
    expect(apiErrorMessage({ error: { code: 'attachment.tooLarge' } })).toBe(
      'Der Dateianhang ist zu groß.'
    )
    expect(apiErrorMessage({ error: { code: 'question.answerTooLarge' } })).toBe(
      'Die Antworten überschreiten das zulässige Größenlimit.'
    )
    expect(apiErrorMessage({ error: { code: 'attachment.invalidDocument' } })).toBe(
      'Das Dokument ist beschädigt, geschützt oder überschreitet die Verarbeitungsgrenzen.'
    )
    expect(apiErrorMessage({ error: { code: 'agent.requiredWebSearchMissing' } })).toBe(
      'Das Modell hat die verpflichtende Websuche auch nach mehreren Versuchen nicht ausgeführt.'
    )
    expect(apiErrorMessage({ error: { code: 'agent.requiredMemoryWriteMissing' } })).toBe(
      'Das Modell hat den begonnenen Memory-Schreibvorgang auch nach mehreren Versuchen nicht abgeschlossen.'
    )
    expect(apiErrorMessage({ error: { code: 'agent.requiredOpenScadSourceReviewMissing' } })).toBe(
      'Das Modell hat die gerenderte OpenSCAD-Datei auch nach mehreren Versuchen nicht geprüft.'
    )
    expect(apiErrorMessage({ error: { code: 'agent.researchSourcesInvalid' } })).toBe(
      'Die recherchierte Antwort konnte nicht ausreichend mit aktuellen Quellen belegt werden.'
    )
    expect(apiErrorMessage({ error: { code: 'agent.emptyResponse' } })).toBe(
      'Das Modell hat auch nach einem erneuten Versuch keine verwertbare Antwort geliefert.'
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
    expect(apiErrorMessage({ error: { code: 'skill.draftInvalid' } })).toBe(
      'Der Skill-Entwurf ist ungültig. Prüfe die Entwurfsdatei und versuche es erneut.'
    )
    expect(apiErrorMessage({ error: { code: 'skill.creationModelFailed' } })).toBe(
      'Der Skill-Entwurf konnte vom ausgewählten Modell nicht erstellt werden.'
    )
    expect(apiErrorMessage({ error: { code: 'skill.changed' } })).toBe(
      'Der Skill wurde zwischenzeitlich geändert. Starte die Bearbeitung erneut.'
    )
    expect(apiErrorMessage({ error: { code: 'skill.notFound' } })).toBe(
      'Der Skill wurde nicht gefunden.'
    )
    expect(apiErrorMessage({ error: { code: 'skill.duplicate' } })).toBe(
      'Ein Skill mit diesem Namen ist bereits vorhanden.'
    )
    expect(apiErrorMessage({ error: { code: 'skill.limitReached' } })).toBe(
      'Die maximale Anzahl persönlicher Skills ist erreicht.'
    )
    expect(apiErrorMessage({ error: { code: 'skill.inUse' } })).toBe(
      'Der Skill wird noch von einer Automation verwendet und kann nicht gelöscht werden.'
    )
    expect(apiErrorMessage({ error: { code: 'automation.scheduleInvalid' } })).toBe(
      'Der Zeitplan ist ungültig.'
    )
    expect(apiErrorMessage({ error: { code: 'automation.alreadyRunning' } })).toBe(
      'Für diese Automation läuft bereits ein Auftrag.'
    )
    expect(apiErrorMessage({ error: { code: 'automation.archived' } })).toBe(
      'Diese Automation ist archiviert.'
    )
    expect(apiErrorMessage({
      error: { code: 'session.automationOutputRequired', params: { automationCount: 2 } }
    })).toBe('Die Session wird von 2 Automationen als Ausgabeziel benötigt.')
    expect(apiErrorMessage({ error: { code: 'privacy.pseudonymizationUnavailable' } })).toBe(
      'Die lokale Pseudonymisierung ist derzeit nicht verfügbar.'
    )
    expect(apiErrorMessage({ error: { code: 'video.frameExtractionFailed' } })).toBe(
      'Die Einzelbilder des Videos konnten nicht extrahiert werden.'
    )
    expect(apiErrorMessage({
      error: { code: 'provider.timeout', params: { timeoutSeconds: 420, retryable: true } }
    })).toBe('Der Provider hat nicht innerhalb von 420 Sekunden geantwortet.')
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
    expect(apiErrorMessage({
      error: {
        code: 'validation.tooManyItems',
        field: 'audio',
        params: { maxItems: 4 }
      }
    })).toBe('Die Audioauswahl darf höchstens 4 Einträge enthalten.')
  })

  it('translates execution, process and transfer errors without unsafe retry advice', () => {
    const codes = [
      'executionAgent.alreadyPaired',
      'executionAgent.notFound',
      'executionAgent.operationUnavailable',
      'executionAgent.outcomeUnknown',
      'executionAgent.pairingTokenExpired',
      'executionAgent.pairingTokenInvalid',
      'executionAgent.protocolError',
      'executionAgent.replaced',
      'executionAgent.requestTooLarge',
      'executionAgent.responseTooLarge',
      'executionAgent.revoked',
      'executionAgent.timeout',
      'executionAgent.unavailable',
      'process.interrupted',
      'process.cancelled',
      'process.failed',
      'process.notAllowed',
      'process.outcomeUnknown',
      'process.outputTooLarge',
      'process.startFailed',
      'process.timedOut',
      'process.unavailable',
      'transfer.expired',
      'transfer.invalidContent',
      'transfer.invalidTicket',
      'transfer.tooLarge'
    ]

    for (const code of codes) {
      expect(apiErrorMessage({ error: { code } })).not.toBe('Die Aktion ist fehlgeschlagen.')
    }
    for (const code of [
      'executionAgent.outcomeUnknown',
      'process.outcomeUnknown',
      'integration.outcomeUnknown'
    ]) {
      expect(apiErrorMessage({ error: { code } })).toContain('nicht blind')
    }
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

  it('prefers German translations over backend messages', () => {
    expect(apiErrorMessage({
      error: {
        code: 'agent.requiredWebSearchMissing',
        message: 'The required web search was not performed.',
        params: {}
      }
    })).toBe('Das Modell hat die verpflichtende Websuche auch nach mehreren Versuchen nicht ausgeführt.')
    expect(apiErrorMessage({
      error: { code: 'future.unknown', message: 'Secret upstream details' }
    })).toBe('Die Aktion ist fehlgeschlagen.')
  })

  it('decodes structured blob errors before localizing them', async () => {
    for (const [code, message] of [
      [
        'file.changed',
        'Die Datei wurde seit dem Öffnen geändert. Öffne sie erneut und übernimm deine Änderungen in die aktuelle Version.'
      ],
      ['openscad.renderFailed', 'Das OpenSCAD-Modell konnte nicht gerendert werden.']
    ]) {
      const source = {
        response: {
          status: 409,
          data: new Blob([JSON.stringify({ error: { code } })], { type: 'application/json' })
        }
      }
      const decoded = await decodeBlobApiError(source)
      expect(decoded).toBe(source)
      expect(apiErrorMessage(decoded)).toBe(message)
    }
  })
})
