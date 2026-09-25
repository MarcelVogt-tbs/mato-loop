import { describe, expect, it } from 'vitest'

import {
  controlCenterIntegrationIds,
  controlCenterIntegrationManifest,
  tbsDeskControlCenterAvailable
} from '../src/app/control-center/manifest'

describe('tbs.Desk Control Center manifest', () => {
  it('registers the ticket app', () => {
    expect(controlCenterIntegrationManifest.find((item) => item.id === 'tbs-desk')).toEqual({
      id: 'tbs-desk', label: 'tbs.Desk', icon: 'confirmation_number'
    })
    expect(controlCenterIntegrationIds.has('tbs-desk')).toBe(true)
  })

  it('offers an enabled personal account before the session is persisted', () => {
    expect(tbsDeskControlCenterAvailable({ tbsDesk: { accounts: [{ enabled: false }] } })).toBe(false)
    expect(tbsDeskControlCenterAvailable({ tbsDesk: { accounts: [{ enabled: true }] } })).toBe(true)
  })
})
