import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), post: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useChannelStore } from '../src/stores/channels'

const providers = [{ provider: 'telegram', label: 'Telegram', allowed: true }]
const endpoint = {
  id: 'telegram-1', provider: 'telegram', name: 'Privat', enabled: true,
  pairingState: 'paired', connected: true
}

function mockChannelReads() {
  api.get.mockImplementation((url) => Promise.resolve({
    data: url === '/channels/providers' ? { enabled: true, providers } : { endpoints: [endpoint] }
  }))
}

describe('channel store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads provider policy and personal endpoints', async () => {
    mockChannelReads()
    const store = useChannelStore()

    await store.fetchAll()

    expect(api.get).toHaveBeenCalledWith('/channels/providers')
    expect(api.get).toHaveBeenCalledWith('/channels/endpoints')
    expect(store.providers).toEqual(providers)
    expect(store.enabled).toBe(true)
    expect(store.endpoints).toEqual([endpoint])
    expect(store.loaded).toBe(true)
    expect(store.loadFailed).toBe(false)
    expect(store.loading).toBe(false)
  })

  it('marks a failed load without replacing existing channel state', async () => {
    api.get.mockRejectedValue(new Error('offline'))
    const store = useChannelStore()
    store.endpoints = [endpoint]

    await expect(store.fetchAll()).rejects.toThrow('offline')

    expect(store.endpoints).toEqual([endpoint])
    expect(store.loaded).toBe(false)
    expect(store.loadFailed).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('updates global enablement separately from provider allowance', async () => {
    mockChannelReads()
    api.put.mockResolvedValue({ data: { enabled: false } })
    const store = useChannelStore()

    await store.setProviderAllowed('telegram', false)
    await store.setEnabled(false)

    expect(api.put).toHaveBeenCalledWith('/channels/providers/telegram', { allowed: false })
    expect(api.get).toHaveBeenCalledWith('/channels/providers')
    expect(api.put).toHaveBeenCalledWith('/channels/settings', { enabled: false })
    expect(store.enabled).toBe(false)
  })

  it('dispatches Telegram mutations and refreshes personal endpoints', async () => {
    mockChannelReads()
    api.post.mockResolvedValue({ data: endpoint })
    api.put.mockResolvedValue({ data: endpoint })
    api.delete.mockResolvedValue({})
    const store = useChannelStore()
    const payload = { name: 'Privat', token: 'secret', enabled: true }

    await store.createTelegram(payload)
    await store.updateTelegram(endpoint.id, { ...payload, token: '' })
    await store.testTelegram(endpoint.id)
    await store.startTelegramPairing(endpoint.id)
    await store.removeTelegram(endpoint.id)

    expect(api.post).toHaveBeenCalledWith('/channels/telegram/endpoints', payload)
    expect(api.put).toHaveBeenCalledWith('/channels/telegram/endpoints/telegram-1', {
      name: 'Privat', enabled: true
    })
    expect(api.post).toHaveBeenCalledWith('/channels/telegram/endpoints/telegram-1/test')
    expect(api.post).toHaveBeenCalledWith('/channels/telegram/endpoints/telegram-1/pairing')
    expect(api.delete).toHaveBeenCalledWith('/channels/telegram/endpoints/telegram-1')
    expect(api.get).toHaveBeenCalledTimes(5)
    expect(store.endpoints).toEqual([endpoint])
  })
})
