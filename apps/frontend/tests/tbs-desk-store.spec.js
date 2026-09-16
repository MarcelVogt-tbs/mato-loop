import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))
vi.mock('src/boot/axios', () => ({ api }))

import { useIntegrationStore } from '../src/stores/integrations'
import { useTbsDeskStore } from '../src/stores/tbs-desk'

const integrationsResponse = {
  configured: true,
  accounts: [{
    id: 'tbs-1', name: 'Support', integrationType: 'support', providerType: 'tbs-desk',
    enabled: true, hasCredential: true,
    config: { baseUrl: 'https://support.example.com', username: 'mato' }
  }]
}

describe('tbs.Desk stores', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('dispatches personal integration CRUD and omits a blank edit password', async () => {
    api.get.mockResolvedValue({ data: integrationsResponse })
    api.post.mockResolvedValue({ data: { status: 'ok' } })
    api.put.mockResolvedValue({})
    api.delete.mockResolvedValue({})
    const store = useIntegrationStore()
    store.selectScope('personal')
    const payload = {
      name: 'Support', baseUrl: 'https://support.example.com', username: 'mato',
      password: 'secret', enabled: true
    }

    await store.fetchTbsDesk()
    await store.createTbsDesk(payload)
    await store.updateTbsDesk('tbs-1', { ...payload, password: '' })
    await store.testTbsDesk('tbs-1')
    await store.removeTbsDesk('tbs-1')

    expect(api.get).toHaveBeenCalledWith('/integrations/support/tbs-desk')
    expect(api.post).toHaveBeenCalledWith('/integrations/support/tbs-desk', payload)
    expect(api.put).toHaveBeenCalledWith('/integrations/support/tbs-desk/tbs-1', {
      name: 'Support', baseUrl: 'https://support.example.com', username: 'mato', enabled: true
    })
    expect(api.post).toHaveBeenCalledWith('/integrations/support/tbs-desk/tbs-1/test')
    expect(api.delete).toHaveBeenCalledWith('/integrations/support/tbs-desk/tbs-1')
    expect(store.tbsDesk).toEqual(integrationsResponse)
  })

  it('loads canonical session state, customer clearing and ticket details', async () => {
    const state = { available: true, selectedCustomer: null, candidates: [], tickets: [], questionId: null }
    const cleared = { ...state, candidates: [{ id: 'customer-1', label: 'Exact label' }], questionId: 'question-1' }
    const detail = { id: 'ticket/1', header: 'Drucker', entries: [] }
    api.get.mockResolvedValueOnce({ data: state }).mockResolvedValueOnce({ data: detail })
    api.post.mockResolvedValue({ data: cleared })
    const store = useTbsDeskStore()

    await store.fetchSession('session-1')
    store.detail = { id: 'old' }
    await store.clearCustomer('session-1')
    await store.fetchTicket('session-1', 'ticket/1')

    expect(api.get).toHaveBeenNthCalledWith(1, '/tbs-desk/sessions/session-1')
    expect(api.post).toHaveBeenCalledWith('/tbs-desk/sessions/session-1/clear-customer')
    expect(api.get).toHaveBeenNthCalledWith(2, '/tbs-desk/sessions/session-1/tickets/ticket%2F1')
    expect(store.state).toEqual(cleared)
    expect(store.detail).toEqual(detail)
  })
})
