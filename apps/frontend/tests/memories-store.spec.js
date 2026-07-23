import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn()
  }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useMemoryStore } from '../src/stores/memories'

describe('memory store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads a server-paginated and filtered memory list', async () => {
    api.get.mockResolvedValue({
      data: {
        items: [{ id: 'memory-1', content: 'Bevorzugt kurze Antworten' }],
        total: 21,
        offset: 20,
        limit: 10
      }
    })
    const store = useMemoryStore()

    await store.fetchMemories({
      query: 'Antworten',
      category: 'preference',
      sourceType: 'manual',
      trustLevel: 'private',
      retrievalLevel: 'instant',
      offset: 20,
      limit: 10
    })

    expect(api.get).toHaveBeenCalledWith('/memories', {
      params: {
        offset: 20,
        limit: 10,
        query: 'Antworten',
        category: 'preference',
        sourceType: 'manual',
        trustLevel: 'private',
        retrievalLevel: 'instant'
      }
    })
    expect(store.items).toHaveLength(1)
    expect(store.total).toBe(21)
    expect(store.offset).toBe(20)
  })

  it('uses camelCase memory mutation payloads and never sends source fields', async () => {
    const memory = {
      id: 'memory-1',
      content: 'Nur lokal ausführen',
      category: 'preference',
      trustLevel: 'private',
      retrievalLevel: 'retrieve'
    }
    api.post.mockResolvedValue({ data: memory })
    api.patch.mockResolvedValue({ data: memory })
    const store = useMemoryStore()
    const payload = {
      content: memory.content,
      category: memory.category,
      trustLevel: memory.trustLevel,
      retrievalLevel: memory.retrievalLevel
    }

    await store.createMemory(payload)
    await store.updateMemory(memory.id, payload)

    expect(api.post).toHaveBeenCalledWith('/memories', payload)
    expect(api.patch).toHaveBeenCalledWith('/memories/memory-1', payload)
  })

  it('acknowledges exact rendered memory IDs and updates their local seen state', async () => {
    api.post.mockResolvedValue({ data: { acknowledged: 1 } })
    const store = useMemoryStore()
    store.items = [
      { id: 'memory-1', seenAt: null },
      { id: 'memory-2', seenAt: null }
    ]

    const result = await store.acknowledgeMemories(['memory-1'])

    expect(api.post).toHaveBeenCalledWith('/memories/acknowledgements', {
      memoryIds: ['memory-1']
    })
    expect(result).toEqual({ acknowledged: 1 })
    expect(store.items[0].seenAt).toBeTruthy()
    expect(store.items[1].seenAt).toBeNull()
  })

  it('loads and saves dreaming settings with camelCase fields', async () => {
    const settings = {
      enabled: true,
      maxContextItems: 12,
      maxContextCharacters: 24000,
      automaticDreamingEnabled: true,
      dreamingInstructions: 'Nur bestätigte Präferenzen',
      dreamingIntervalMinutes: 30,
      dreamingIdleMinutes: 10,
      dreamingMaxBatchMessages: 50,
      dreamingMaxCandidates: 8,
      effectiveAutomaticDreamingEnabled: true,
      automaticDreamingUnavailableReason: null,
      manualDreamingAvailable: true,
      manualDreamingUnavailableReason: null
    }
    api.get.mockResolvedValue({ data: settings })
    api.patch.mockResolvedValue({ data: settings })
    const store = useMemoryStore()

    await store.fetchSettings()
    await store.saveSettings(settings)

    expect(api.get).toHaveBeenCalledWith('/memories/settings')
    expect(api.patch).toHaveBeenCalledWith('/memories/settings', settings)
    expect(store.settings).toEqual(settings)
  })

  it('starts a scoped dream run and refreshes the run list', async () => {
    api.post.mockResolvedValue({ data: { id: 'run-1', status: 'queued' } })
    api.get.mockResolvedValue({ data: { items: [{ id: 'run-1', status: 'queued' }] } })
    const store = useMemoryStore()

    await store.startDreamRun('session-1')

    expect(api.post).toHaveBeenCalledWith('/memories/dream-runs', { sessionId: 'session-1' })
    expect(api.get).toHaveBeenCalledWith('/memories/dream-runs')
    expect(store.dreamRuns).toEqual([{ id: 'run-1', status: 'queued' }])
  })
})
