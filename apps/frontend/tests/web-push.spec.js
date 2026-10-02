import { describe, expect, it } from 'vitest'
import { applicationServerKey, parsePushPayload, pushTargetPath } from '../src/utils/web-push'

const SESSION_ID = '3e6e1d85-4c5e-4fb9-8b8b-77542d0e24ad'
const AUTOMATION_ID = 'e97be71c-7b81-45f5-8705-2c94f44882f0'
const RUN_ID = '9449fe21-ca91-4dc3-b3f0-513623841951'

describe('web push contract', () => {
  it('decodes a URL-safe application server key', () => {
    expect(applicationServerKey('AQID-_8')).toEqual(new Uint8Array([1, 2, 3, 251, 255]))
  })

  it('accepts version 1 notifications and keeps clicks on controlled routes', () => {
    const payload = parsePushPayload({
      version: 1,
      notification: {
        title: 'Automation abgeschlossen',
        body: 'Der Bericht ist bereit.',
        notificationId: RUN_ID,
        target: { type: 'session', sessionId: SESSION_ID }
      }
    })

    expect(payload).toEqual({
      title: 'Automation abgeschlossen',
      options: {
        body: 'Der Bericht ist bereit.',
        icon: '/icons/icon-192.png',
        data: {
          version: 1,
          notificationId: RUN_ID,
          target: { type: 'session', sessionId: SESSION_ID }
        }
      }
    })
    expect(pushTargetPath(payload.options.data.target)).toBe(`/?session=${SESSION_ID}`)
    expect(pushTargetPath({
      type: 'automationRun', automationId: AUTOMATION_ID, runId: RUN_ID
    })).toBe(`/settings/automations/${AUTOMATION_ID}?run=${RUN_ID}`)
    expect(pushTargetPath({ type: 'actions' })).toBe('/?controlCenter=notifications')
  })

  it('shows a safe fallback for unknown versions and ignores arbitrary click destinations', () => {
    expect(parsePushPayload({ version: 2, notification: { title: 'Unbekannt' } })).toEqual({
      title: 'Neue Benachrichtigung',
      options: {
        body: 'Öffne MatoLoop, um die Benachrichtigung anzuzeigen.',
        icon: '/icons/icon-192.png',
        data: { version: 1, target: null }
      }
    })
    expect(pushTargetPath({ type: 'url', url: 'https://example.com' })).toBe('/')
  })
})
