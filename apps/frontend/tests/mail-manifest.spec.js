import { describe, expect, it } from 'vitest'

import {
  controlCenterIntegrationIds,
  controlCenterIntegrationManifest,
  codingControlCenterAvailable,
  mailControlCenterAvailable
} from '../src/control-center/manifest'

describe('mail Control Center manifest', () => {
  const item = controlCenterIntegrationManifest.find((entry) => entry.id === 'mail')

  it('compiles Mail into the integration manifest', () => {
    expect(item).toEqual({ id: 'mail', label: 'E-Mail', icon: 'mail' })
    expect(controlCenterIntegrationIds.has('mail')).toBe(true)
  })

  it('offers Mail only after at least one effective account loaded', () => {
    expect(mailControlCenterAvailable({ accountsLoaded: false, accounts: [{ id: '1' }] })).toBe(false)
    expect(mailControlCenterAvailable({ accountsLoaded: true, accounts: [] })).toBe(false)
    expect(mailControlCenterAvailable({ accountsLoaded: true, accounts: [{ id: '1' }] })).toBe(true)
  })

  it('offers OpenCode for every selected project', () => {
    expect(controlCenterIntegrationIds.has('coding')).toBe(true)
    expect(codingControlCenterAvailable(null)).toBe(false)
    expect(codingControlCenterAvailable({ codingService: null })).toBe(true)
    expect(codingControlCenterAvailable({ codingService: { adapterType: 'opencode' } })).toBe(true)
  })
})
