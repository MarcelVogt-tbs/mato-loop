import { describe, expect, it } from 'vitest'

import {
  controlCenterIntegrationIds,
  controlCenterIntegrationManifest,
  tbsDeskControlCenterAvailable
} from '../src/control-center/manifest'

describe('tbs.Desk Control Center manifest', () => {
  it('registers the ticket app', () => {
    expect(controlCenterIntegrationManifest.find((item) => item.id === 'tbs-desk')).toEqual({
      id: 'tbs-desk', label: 'tbs.Desk', icon: 'confirmation_number'
    })
    expect(controlCenterIntegrationIds.has('tbs-desk')).toBe(true)
  })

  it('requires a persisted session and an enabled personal account', () => {
    expect(tbsDeskControlCenterAvailable(null, { tbsDesk: { accounts: [{ enabled: true }] } })).toBe(false)
    expect(tbsDeskControlCenterAvailable({ id: 'session-1' }, { tbsDesk: { accounts: [{ enabled: false }] } })).toBe(false)
    expect(tbsDeskControlCenterAvailable({ id: 'session-1' }, { tbsDesk: { accounts: [{ enabled: true }] } })).toBe(true)
  })
})
