import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    delete: vi.fn(),
    get: vi.fn(),
    patch: vi.fn(),
    post: vi.fn(),
    put: vi.fn()
  }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useAgentIdentityStore } from '../src/stores/agent-identity'

const settings = (values = {}) => ({
  scope: 'personal',
  configuredDisplayName: null,
  effectiveDisplayName: 'MatoLoop',
  personalPersonasEnabled: true,
  personas: [{ id: 'global', name: 'Standard', instructions: 'Freundlich.', scope: 'instance' }],
  defaultPersonaId: 'global',
  profilePersonas: {},
  profiles: [],
  ...values
})

describe('agent identity store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('uses MatoLoop as a synchronous fallback and loads the effective name', async () => {
    api.get.mockResolvedValue({ data: { displayName: 'Ada' } })
    const identity = useAgentIdentityStore()

    expect(identity.displayName).toBe('MatoLoop')
    await identity.fetchIdentity()

    expect(api.get).toHaveBeenCalledWith('/agent-identity', undefined)
    expect(identity.displayName).toBe('Ada')
    expect(identity.loaded).toBe(true)
  })

  it('loads scoped settings without replacing them with another scope', async () => {
    api.get.mockResolvedValue({
      data: settings({
        configuredDisplayName: 'Ada',
        effectiveDisplayName: 'Ada',
        profilePersonas: { 'global-agent': 'global' }
      })
    })
    const identity = useAgentIdentityStore()

    await identity.fetchSettings('personal')

    expect(api.get).toHaveBeenCalledWith('/agent-identity/settings', {
      params: { scope: 'personal' }
    })
    expect(identity.configuredDisplayName).toBe('Ada')
    expect(identity.displayName).toBe('Ada')
    expect(identity.profilePersonas).toEqual({ 'global-agent': 'global' })
  })

  it('persists scoped names and persona overrides with exact payloads', async () => {
    const identity = useAgentIdentityStore()
    api.patch.mockResolvedValue({
      data: settings({ configuredDisplayName: 'Ada', effectiveDisplayName: 'Ada' })
    })

    await identity.saveDisplayName('Ada')

    expect(api.patch).toHaveBeenCalledWith(
      '/agent-identity/settings/display-name',
      { displayName: 'Ada' },
      { params: { scope: 'personal' } }
    )

    api.put.mockResolvedValue({ data: settings({ profilePersonas: { 'global-agent': 'global' } }) })
    await identity.savePersonaOverride('global-agent', 'global')
    expect(api.put).toHaveBeenCalledWith(
      '/agent-identity/persona-overrides/global-agent',
      { personaId: 'global' },
      { params: { scope: 'personal' } }
    )

    api.delete.mockResolvedValue({ data: settings() })
    await identity.deletePersonaOverride('global-agent')
    expect(api.delete).toHaveBeenCalledWith('/agent-identity/persona-overrides/global-agent', {
      params: { scope: 'personal' }
    })
    expect(identity.profilePersonas).toEqual({})
  })

  it('does not let an older fetch overwrite an SSE update', async () => {
    let resolveFetch
    api.get.mockImplementation(() => new Promise((resolve) => { resolveFetch = resolve }))
    const identity = useAgentIdentityStore()

    const loading = identity.fetchIdentity()
    identity.assignIdentity({ displayName: 'Neu' })
    resolveFetch({ data: { displayName: 'Alt' } })
    await loading

    expect(identity.displayName).toBe('Neu')
  })
})
