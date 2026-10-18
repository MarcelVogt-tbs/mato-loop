import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), post: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useAgentProfileStore } from '../src/stores/agent-profiles'
import { useChatStore } from '../src/features/chat/store'

describe('agent profiles store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('switches one custom profile without submitting its definition', async () => {
    api.patch.mockResolvedValue({ data: {} })
    const store = useAgentProfileStore()

    await store.setAgentEnabled('agent-id', false, 'instance')

    expect(api.patch).toHaveBeenCalledWith(
      '/agent-profiles/agent-id/enabled', { enabled: false }, { params: { scope: 'instance' } }
    )
  })

  it('loads selectable agents and the personal-agent gate', async () => {
    const agents = [
      { id: 'global-1', name: 'Recherche', scope: 'global' },
      { id: 'personal-1', name: 'Mein Agent', scope: 'personal' }
    ]
    api.get.mockResolvedValue({
      data: {
        defaultAgentProfileId: 'global-1',
        personalAgentsEnabled: false,
        effectiveAgents: agents
      }
    })
    const store = useAgentProfileStore()

    await store.fetchAgents()

    expect(api.get).toHaveBeenCalledWith('/agent-profiles')
    expect(store.agents).toEqual(agents)
    expect(store.personalAgentsEnabled).toBe(false)
    expect(store.defaultAgentProfileId).toBe('global-1')
  })

  it('uses standard global and personal REST paths for CRUD', async () => {
    const payload = {
      name: 'Recherche',
      description: 'Prüft Quellen',
      baseProfileId: 'builtin-1',
      overrides: {
        personaId: 'persona-1',
        instructions: 'Arbeite sorgfältig.',
        maxToolCalls: null,
        integrationsEnabledByDefault: false,
        publicPermissions: { policies: { 'network.search': 'auto' } },
        privatePermissions: { policies: { 'network.search': 'ask' } }
      }
    }
    api.post.mockResolvedValue({ data: { agentProfileId: 'global-1' } })
    api.put.mockResolvedValue({ data: {} })
    api.delete.mockResolvedValue({})
    api.get
      .mockResolvedValueOnce({ data: { effectiveAgents: [{ id: 'global-1', ...payload, scope: 'instance' }] } })
      .mockResolvedValueOnce({ data: { effectiveAgents: [{ id: 'global-1', ...payload, name: 'Analyse', scope: 'instance' }] } })
    const store = useAgentProfileStore()
    const chat = useChatStore()
    store.selectScope('instance')

    await store.createAgent(payload)
    await store.updateAgent('global-1', { ...payload, name: 'Analyse' })
    expect(store.byId('global-1')).toMatchObject({
      baseProfileId: 'builtin-1',
      overrides: payload.overrides
    })
    store.defaultAgentProfileId = 'default-1'
    chat.current = { id: 'session-1', agentProfileId: 'global-1' }
    chat.sessionLists.standalone.items = [
      { id: 'session-1', agentProfileId: 'global-1' },
      { id: 'session-2', agentProfileId: 'other-1' }
    ]
    await store.deleteAgent('global-1')

    expect(api.post).toHaveBeenCalledWith('/agent-profiles', payload, { params: { scope: 'instance' } })
    expect(api.put).toHaveBeenCalledWith('/agent-profiles/global-1', { ...payload, name: 'Analyse' }, { params: { scope: 'instance' } })
    expect(api.delete).toHaveBeenCalledWith('/agent-profiles/global-1', { params: { scope: 'instance' } })
    expect(store.agents).toEqual([])
    expect(chat.current.agentProfileId).toBe('default-1')
    expect(chat.sessionLists.standalone.items).toEqual([
      { id: 'session-1', agentProfileId: 'default-1' },
      { id: 'session-2', agentProfileId: 'other-1' }
    ])

    store.selectScope('personal')
    api.post.mockResolvedValue({ data: { agentProfileId: 'personal-1' } })
    api.get.mockResolvedValue({ data: { effectiveAgents: [{ id: 'personal-1', ...payload, scope: 'personal' }] } })
    await store.createAgent(payload)
    expect(api.post).toHaveBeenLastCalledWith('/agent-profiles', payload, { params: { scope: 'personal' } })
    expect(store.agents[0].scope).toBe('personal')
  })

  it('restores personal agents when the instance gate is enabled', async () => {
    const personal = { id: 'personal-1', name: 'Mein Agent', scope: 'personal' }
    api.patch.mockResolvedValue({
      data: { personalAgentsEnabled: true, effectiveAgents: [personal] }
    })
    const store = useAgentProfileStore()
    store.personalAgentsEnabled = false

    await store.setPersonalAgentsEnabled(true)

    expect(store.personalAgentsEnabled).toBe(true)
    expect(store.agents).toEqual([personal])
  })

})
