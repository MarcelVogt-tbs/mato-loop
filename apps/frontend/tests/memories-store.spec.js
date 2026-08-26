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
      audience: 'private_only',
      pinned: false,
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
        audience: 'private_only',
        pinned: false
      }
    })
    expect(store.items).toHaveLength(1)
    expect(store.total).toBe(21)
    expect(store.offset).toBe(20)
  })

  it('keeps create host-controlled and includes pinned in an update', async () => {
    const memory = {
      id: 'memory-1',
      content: 'Die bevorzugte Sprache ist Deutsch',
      category: 'identity',
      audience: 'private_only',
      pinned: true,
      pinKind: 'primary_language',
      revision: 4
    }
    api.post.mockResolvedValue({ data: memory })
    api.patch.mockResolvedValue({ data: memory })
    const store = useMemoryStore()
    store.items = [{ id: memory.id, content: 'Alt' }]
    const contentPayload = {
      content: memory.content,
      category: memory.category,
      audience: memory.audience,
      pinKind: memory.pinKind
    }
    const suppliedPayload = {
      ...contentPayload,
      sourceType: 'manual',
      pinned: memory.pinned,
      expectedRevision: memory.revision
    }

    await store.createMemory(suppliedPayload)
    await store.updateMemory(memory.id, suppliedPayload)

    expect(api.post).toHaveBeenCalledWith('/memories', contentPayload)
    expect(api.patch).toHaveBeenCalledWith('/memories/memory-1', {
      ...contentPayload,
      pinned: true,
      expectedRevision: 4
    })
    expect(store.items[0]).toEqual(memory)
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

  it('deletes a memory with its expected revision', async () => {
    api.delete.mockResolvedValue({})
    const store = useMemoryStore()
    store.items = [{ id: 'memory-1', revision: 3 }]
    store.total = 1

    await store.deleteMemory('memory-1', 3)

    expect(api.delete).toHaveBeenCalledWith('/memories/memory-1', {
      params: { expectedRevision: 3 }
    })
    expect(store.items).toEqual([])
    expect(store.total).toBe(0)
  })

  it('preserves local memory state when a revision-protected update fails', async () => {
    api.patch.mockRejectedValue(new Error('request failed'))
    const store = useMemoryStore()
    store.items = [{ id: 'memory-1', content: 'Aktuell', revision: 5 }]

    await expect(store.updateMemory('memory-1', {
      content: 'Veraltet',
      category: 'fact',
      audience: 'both',
      pinned: false,
      pinKind: null,
      expectedRevision: 4
    })).rejects.toThrow('request failed')

    expect(store.items).toEqual([{ id: 'memory-1', content: 'Aktuell', revision: 5 }])
  })

  it('loads and saves memory and dreaming settings with camelCase fields', async () => {
    const settings = {
      enabled: true,
      automaticDreamingEnabled: true,
      dreamingInstructions: 'Nur bestätigte Präferenzen',
      dreamingIntervalMinutes: 30,
      dreamingIdleMinutes: 10,
      dreamingMaxBatchMessages: 50,
      dreamingMaxCandidates: 8,
      dreamingUpdatePolicy: 'auto',
      dreamingRemovePolicy: 'ask',
      dreamingMaxPinnedMemories: 10,
      automaticMemorySnippetLimit: 2,
      automaticSessionSnippetLimit: 1,
      recentSessionSnippetLimit: 2,
      automaticContextMaxTokens: 1024,
      pinnedMemoryCount: 4,
      effectiveAutomaticDreamingEnabled: true,
      automaticDreamingUnavailableReason: null,
      manualDreamingAvailable: true,
      manualDreamingUnavailableReason: null
    }
    api.get.mockResolvedValue({ data: settings })
    api.patch.mockResolvedValue({ data: settings })
    const store = useMemoryStore()
    const savePayload = {
      dreamingInstructions: settings.dreamingInstructions,
      dreamingIntervalMinutes: settings.dreamingIntervalMinutes,
      dreamingIdleMinutes: settings.dreamingIdleMinutes,
      dreamingMaxBatchMessages: settings.dreamingMaxBatchMessages,
      dreamingMaxCandidates: settings.dreamingMaxCandidates,
      dreamingUpdatePolicy: settings.dreamingUpdatePolicy,
      dreamingRemovePolicy: settings.dreamingRemovePolicy,
      dreamingMaxPinnedMemories: settings.dreamingMaxPinnedMemories,
      automaticMemorySnippetLimit: settings.automaticMemorySnippetLimit,
      automaticSessionSnippetLimit: settings.automaticSessionSnippetLimit,
      recentSessionSnippetLimit: settings.recentSessionSnippetLimit,
      automaticContextMaxTokens: settings.automaticContextMaxTokens
    }

    await store.fetchSettings()
    await store.saveSettings(savePayload)

    expect(api.get).toHaveBeenCalledWith('/memories/settings')
    expect(api.patch).toHaveBeenCalledWith('/memories/settings', savePayload)
    expect(store.settings).toEqual(settings)
  })

  it('starts a scoped dream run and refreshes the run list', async () => {
    api.post.mockResolvedValue({ data: { id: 'run-1', status: 'queued' } })
    api.get.mockResolvedValue({
      data: { items: [{ id: 'run-1', status: 'completed', createdMemories: 0, removedMemories: 2 }] }
    })
    const store = useMemoryStore()

    await store.startDreamRun('session-1')

    expect(api.post).toHaveBeenCalledWith('/memories/dream-runs', { sessionId: 'session-1' })
    expect(api.get).toHaveBeenCalledWith('/memories/dream-runs')
    expect(store.dreamRuns).toEqual([
      { id: 'run-1', status: 'completed', createdMemories: 0, removedMemories: 2 }
    ])
  })
})
