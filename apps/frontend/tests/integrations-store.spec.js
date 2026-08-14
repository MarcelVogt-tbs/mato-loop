import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useIntegrationStore } from '../src/stores/integrations'

const searchResponse = {
  configured: true,
  enabled: true,
  effectiveEnabled: true,
  unavailableReason: null,
  providers: [
    {
      id: 'integration-id',
      name: 'Intern',
      integrationType: 'search',
      providerType: 'searxng',
      enabled: true,
      supportsResourcePolicies: false,
      config: { baseUrl: 'https://search.example.com' }
    }
  ]
}

const calendarResponse = {
  configured: true,
  accounts: [
    {
      id: 'calendar-id',
      name: 'Arbeit',
      integrationType: 'calendar',
      providerType: 'caldav',
      enabled: true,
      hasCredential: true,
      supportsResourcePolicies: true,
      config: { baseUrl: 'https://calendar.example.com', username: 'mato' }
    }
  ]
}

const knowledgeResponse = {
  configured: true,
  bases: [
    {
      id: 'knowledge-id',
      name: 'Wiki',
      integrationType: 'knowledge',
      providerType: 'bookstack',
      enabled: true,
      hasCredential: true,
      supportsResourcePolicies: true,
      config: { baseUrl: 'https://wiki.example.com' }
    }
  ]
}

const gitResponse = {
  configured: true,
  instances: [
    {
      id: 'git-id',
      name: 'Projekte',
      integrationType: 'git',
      providerType: 'git',
      executionAgentId: 'agent-1',
      allowNetwork: false,
      enabled: true
    }
  ]
}

const openScadResponse = {
  configured: true,
  instances: [{
    id: 'openscad-id',
    name: 'OpenSCAD',
    integrationType: 'openscad',
    providerType: 'openscad',
    executionAgentId: 'agent-1',
    enabled: true,
    config: { binaryPath: '/usr/bin/openscad' },
    executionLocation: 'agent'
  }]
}

describe('integration store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads only configured external integrations', async () => {
    api.get.mockResolvedValue({ data: searchResponse })
    const store = useIntegrationStore()

    await store.fetchSearch()

    expect(api.get).toHaveBeenCalledWith('/integrations/search')
    expect(store.search.providers.map((item) => item.providerType)).toEqual(['searxng'])
    expect(store.search.providerOptions).toBeUndefined()
    expect(store.loaded).toBe(true)
    expect(store.loadFailed).toBe(false)
  })

  it('marks a failed initial load without inventing an integration', async () => {
    api.get.mockRejectedValue(new Error('offline'))
    const store = useIntegrationStore()

    await expect(store.fetchSearch()).rejects.toThrow('offline')

    expect(store.search.providers).toEqual([])
    expect(store.loaded).toBe(false)
    expect(store.loadFailed).toBe(true)
  })

  it('reloads the canonical contract after creating an integration', async () => {
    api.post.mockResolvedValue({ data: searchResponse })
    api.get.mockResolvedValue({ data: searchResponse })
    const store = useIntegrationStore()
    const payload = {
      integration_type: 'search',
      provider_type: 'searxng',
      name: 'Intern',
      base_url: 'https://search.example.com',
      enabled: true
    }

    await store.createSearch(payload)

    expect(api.post).toHaveBeenCalledWith('/integrations/search/providers', payload)
    expect(store.search.providers).toHaveLength(1)
  })

  it('loads and dispatches calendar account operations', async () => {
    api.get.mockResolvedValue({ data: calendarResponse })
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    api.delete.mockResolvedValue({})
    const store = useIntegrationStore()
    const payload = {
      integration_type: 'calendar',
      provider_type: 'caldav',
      name: 'Arbeit',
      base_url: 'https://calendar.example.com',
      username: 'mato',
      password: 'secret',
      enabled: true
    }

    await store.fetchCalendar()
    await store.createCalendar(payload)
    await store.testCalendar('calendar-id')
    await store.removeCalendar('calendar-id')

    expect(api.get).toHaveBeenCalledWith('/integrations/calendar')
    expect(api.post).toHaveBeenCalledWith('/integrations/calendar/accounts', payload)
    expect(api.post).toHaveBeenCalledWith('/integrations/calendar/accounts/calendar-id/test')
    expect(api.delete).toHaveBeenCalledWith('/integrations/calendar/accounts/calendar-id')
    expect(store.calendar.accounts).toHaveLength(1)
  })

  it('omits a blank password when updating a calendar account', async () => {
    api.get.mockResolvedValue({ data: calendarResponse })
    api.put.mockResolvedValue({ data: calendarResponse })
    const store = useIntegrationStore()

    await store.updateCalendar('calendar-id', {
      name: 'Arbeit',
      password: '',
      enabled: true
    })

    expect(api.put).toHaveBeenCalledWith('/integrations/calendar/accounts/calendar-id', {
      name: 'Arbeit',
      enabled: true
    })
  })

  it('loads and dispatches knowledge base operations', async () => {
    api.get.mockResolvedValue({ data: knowledgeResponse })
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    api.delete.mockResolvedValue({})
    const store = useIntegrationStore()
    const payload = {
      integration_type: 'knowledge',
      provider_type: 'bookstack',
      name: 'Wiki',
      base_url: 'https://wiki.example.com',
      token_id: 'token',
      token_secret: 'secret',
      enabled: true
    }

    await store.fetchKnowledge()
    await store.createKnowledge(payload)
    await store.testKnowledge('knowledge-id')
    await store.removeKnowledge('knowledge-id')

    expect(api.get).toHaveBeenCalledWith('/integrations/knowledge')
    expect(api.post).toHaveBeenCalledWith('/integrations/knowledge/bases', payload)
    expect(api.post).toHaveBeenCalledWith('/integrations/knowledge/bases/knowledge-id/test')
    expect(api.delete).toHaveBeenCalledWith('/integrations/knowledge/bases/knowledge-id')
    expect(store.knowledge.bases).toHaveLength(1)
  })

  it('omits incomplete token fields when updating a knowledge base', async () => {
    api.get.mockResolvedValue({ data: knowledgeResponse })
    api.put.mockResolvedValue({ data: knowledgeResponse })
    const store = useIntegrationStore()

    await store.updateKnowledge('knowledge-id', {
      name: 'Wiki',
      token_id: 'token',
      token_secret: '',
      enabled: true
    })

    expect(api.put).toHaveBeenCalledWith('/integrations/knowledge/bases/knowledge-id', {
      name: 'Wiki',
      enabled: true
    })
  })

  it('loads and dispatches Git instance operations with the Git payload', async () => {
    api.get.mockResolvedValue({ data: gitResponse })
    api.post.mockResolvedValue({})
    api.put.mockResolvedValue({})
    api.delete.mockResolvedValue({})
    const store = useIntegrationStore()
    const payload = {
      executionAgentId: 'agent-1',
      config: { allowNetwork: false },
      name: 'Projekte',
      enabled: true
    }

    await store.fetchGit()
    await store.createGit(payload)
    await store.updateGit('git-id', payload)
    await store.removeGit('git-id')

    expect(api.get).toHaveBeenCalledWith('/integrations/git')
    expect(api.post).toHaveBeenCalledWith('/integrations/git', payload)
    expect(api.put).toHaveBeenCalledWith('/integrations/git/git-id', payload)
    expect(api.delete).toHaveBeenCalledWith('/integrations/git/git-id')
    expect(store.git.instances).toEqual(gitResponse.instances)
  })

  it('loads and dispatches OpenSCAD CRUD, discovery and test operations', async () => {
    api.get.mockResolvedValue({ data: openScadResponse })
    api.post.mockImplementation((url) => Promise.resolve({
      data: url.endsWith('/discover')
        ? { available: true, executablePath: '/opt/openscad', version: '2025.01', headlessPreview: true }
        : { status: 'ok' }
    }))
    api.put.mockResolvedValue({})
    api.delete.mockResolvedValue({})
    const store = useIntegrationStore()
    const payload = {
      executionAgentId: 'agent-1',
      name: 'OpenSCAD',
      config: { binaryPath: '/usr/bin/openscad' },
      enabled: true
    }

    await store.fetchOpenScad()
    await store.createOpenScad(payload)
    await store.updateOpenScad('openscad-id', payload)
    const discovered = await store.discoverOpenScad('agent-1')
    await store.testOpenScad('openscad-id')
    await store.removeOpenScad('openscad-id')

    expect(api.get).toHaveBeenCalledWith('/integrations/openscad')
    expect(api.post).toHaveBeenCalledWith('/integrations/openscad', payload)
    expect(api.put).toHaveBeenCalledWith('/integrations/openscad/openscad-id', payload)
    expect(api.post).toHaveBeenCalledWith('/integrations/openscad/discover', { executionAgentId: 'agent-1' })
    expect(api.post).toHaveBeenCalledWith('/integrations/openscad/openscad-id/test')
    expect(api.delete).toHaveBeenCalledWith('/integrations/openscad/openscad-id')
    expect(discovered.executablePath).toBe('/opt/openscad')
    expect(store.openscad.instances).toEqual(openScadResponse.instances)
  })

  it('keeps loading active until concurrent integration requests finish', async () => {
    let resolveSearch
    let resolveCalendar
    api.get.mockImplementation((url) => new Promise((resolve) => {
      if (url === '/integrations/search') resolveSearch = resolve
      else resolveCalendar = resolve
    }))
    const store = useIntegrationStore()

    const search = store.fetchSearch()
    const calendar = store.fetchCalendar()
    expect(store.loading).toBe(true)

    resolveSearch({ data: searchResponse })
    await search
    expect(store.loading).toBe(true)
    expect(store.pendingLoads).toBe(1)

    resolveCalendar({ data: calendarResponse })
    await calendar
    expect(store.loading).toBe(false)
    expect(store.pendingLoads).toBe(0)
  })

  it('loads and saves resource policies for one integration', async () => {
    const access = {
      integrationId: 'integration-id',
      defaultResourceKey: 'calendar:work',
      defaultTaskListKey: null,
      actions: [
        {
          capability: 'calendar.read',
          label: 'Lesen',
          allowedPolicies: ['disabled', 'ask', 'auto'],
          globalPolicy: 'ask',
          integrationPolicy: 'auto',
          effectiveIntegrationPolicy: 'auto'
        }
      ],
      resources: [
        {
          key: 'calendar:work',
          groupKey: 'work',
          label: 'Arbeit',
          applicableCapabilities: ['calendar.read'],
          policies: { 'calendar.read': 'auto' },
          effectivePolicies: { 'calendar.read': 'auto' },
          hardUnavailableCapabilities: []
        }
      ]
    }
    api.get.mockResolvedValue({ data: access })
    api.put.mockResolvedValue({ data: access })
    const store = useIntegrationStore()

    await store.fetchAccess('integration-id')
    await store.saveAccess(
      'integration-id',
      {
        policies: { 'calendar.read': 'auto' },
        resources: { 'calendar:work': { 'calendar.read': 'auto' } },
        defaultResourceKey: 'calendar:work',
        defaultTaskListKey: null
      }
    )

    expect(api.get).toHaveBeenCalledWith('/integrations/integration-id/access')
    expect(api.put).toHaveBeenCalledWith('/integrations/integration-id/access', {
      policies: { 'calendar.read': 'auto' },
      resources: { 'calendar:work': { 'calendar.read': 'auto' } },
      defaultResourceKey: 'calendar:work',
      defaultTaskListKey: null
    })
    expect(store.accessByIntegration['integration-id']).toEqual(access)
  })
})
