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
  personalDisplayNamesEnabled: true,
  personalPersonasEnabled: true,
  defaultPersonaId: null,
  effectiveDefaultPersonaId: 'instance-default',
  personas: [{
    id: 'instance-default',
    name: 'Standard',
    instructions: '',
    scope: 'instance',
    effective: true
  }],
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
        personas: [
          ...settings().personas,
          {
            id: 'personal-one',
            name: 'Direkt',
            instructions: 'Antworte direkt.',
            scope: 'personal',
            effective: true
          }
        ]
      })
    })
    const identity = useAgentIdentityStore()

    await identity.fetchSettings('personal')

    expect(api.get).toHaveBeenCalledWith('/agent-identity/settings', {
      params: { scope: 'personal' }
    })
    expect(identity.configuredDisplayName).toBe('Ada')
    expect(identity.displayName).toBe('Ada')
    expect(identity.scopedPersonas.map((persona) => persona.id)).toEqual(['personal-one'])
  })

  it('persists scoped names and persona CRUD with exact payloads', async () => {
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

    api.post.mockResolvedValue({
      data: settings({
        personas: [
          ...settings().personas,
          {
            id: 'personal-one',
            name: 'Direkt',
            instructions: 'Kurz.',
            scope: 'personal',
            effective: true
          }
        ]
      })
    })
    await identity.createPersona({ name: 'Direkt', instructions: 'Kurz.' })
    expect(api.post).toHaveBeenCalledWith(
      '/agent-identity/personas',
      { name: 'Direkt', instructions: 'Kurz.' },
      { params: { scope: 'personal' } }
    )

    api.delete.mockResolvedValue({ data: settings() })
    await identity.deletePersona('personal-one')
    expect(api.delete).toHaveBeenCalledWith('/agent-identity/personas/personal-one', {
      params: { scope: 'personal' }
    })
    expect(identity.personaById('personal-one')).toBeNull()
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
