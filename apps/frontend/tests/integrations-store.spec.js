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
