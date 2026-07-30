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

  it('loads and saves resource policies for one integration', async () => {
    const access = {
      integrationId: 'integration-id',
      actions: [{ capability: 'calendar.read', label: 'Lesen', globalPolicy: 'ask' }],
      resources: [
        {
          key: 'calendar:work',
          label: 'Arbeit',
          policies: { 'calendar.read': 'auto' },
          effectivePolicies: { 'calendar.read': 'auto' }
        }
      ]
    }
    api.get.mockResolvedValue({ data: access })
    api.put.mockResolvedValue({ data: access })
    const store = useIntegrationStore()

    await store.fetchAccess('integration-id')
    await store.saveAccess('integration-id', {
      'calendar:work': { 'calendar.read': 'auto' }
    })

    expect(api.get).toHaveBeenCalledWith('/integrations/integration-id/access')
    expect(api.put).toHaveBeenCalledWith('/integrations/integration-id/access', {
      resources: { 'calendar:work': { 'calendar.read': 'auto' } }
    })
    expect(store.accessByIntegration['integration-id']).toEqual(access)
  })
})
