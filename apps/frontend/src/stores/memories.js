import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useMemoryStore = defineStore('memories', {
  state: () => ({
    items: [],
    total: 0,
    offset: 0,
    limit: 20,
    loading: false,
    listRequestId: 0,
    settings: null,
    settingsLoading: false,
    dreamRuns: [],
    dreamRunsLoading: false,
    dreamRunsRequestId: 0,
    dreamRunStarting: false
  }),
  actions: {
    async fetchMemories(filters = {}) {
      const requestId = ++this.listRequestId
      this.loading = true
      try {
        const params = {
          offset: filters.offset ?? this.offset,
          limit: filters.limit ?? this.limit
        }
        for (const key of ['query', 'category', 'sourceType', 'audience']) {
          if (filters[key]) params[key] = filters[key]
        }
        if (filters.pinned != null) params.pinned = filters.pinned
        const { data } = await api.get('/memories', { params })
        if (requestId !== this.listRequestId) return null
        this.items = data.items
        this.total = data.total
        this.offset = data.offset
        this.limit = data.limit
        return data
      } finally {
        if (requestId === this.listRequestId) this.loading = false
      }
    },
    async createMemory({ content, category, audience }) {
      return (await api.post('/memories', { content, category, audience })).data
    },
    async updateMemory(id, { content, category, audience, pinned, expectedRevision }) {
      const { data } = await api.patch(`/memories/${id}`, {
        content, category, audience, pinned, expectedRevision
      })
      const index = this.items.findIndex((item) => item.id === id)
      if (index >= 0) this.items[index] = data
      return data
    },
    async deleteMemory(id, expectedRevision) {
      await api.delete(`/memories/${id}`, { params: { expectedRevision } })
      this.items = this.items.filter((item) => item.id !== id)
      this.total = Math.max(0, this.total - 1)
    },
    async acknowledgeMemories(memoryIds) {
      if (!memoryIds.length) return { acknowledged: 0 }
      const { data } = await api.post('/memories/acknowledgements', { memoryIds })
      const acknowledgedIds = new Set(memoryIds)
      const seenAt = new Date().toISOString()
      this.items = this.items.map((item) => (
        acknowledgedIds.has(item.id) ? { ...item, seenAt: item.seenAt || seenAt } : item
      ))
      return data
    },
    async fetchSettings() {
      this.settingsLoading = true
      try {
        this.settings = (await api.get('/memories/settings')).data
        return this.settings
      } finally {
        this.settingsLoading = false
      }
    },
    async saveSettings(settings) {
      this.settingsLoading = true
      try {
        this.settings = (await api.patch('/memories/settings', settings)).data
        return this.settings
      } finally {
        this.settingsLoading = false
      }
    },
    async fetchDreamRuns() {
      const requestId = ++this.dreamRunsRequestId
      this.dreamRunsLoading = true
      try {
        const { data } = await api.get('/memories/dream-runs')
        if (requestId !== this.dreamRunsRequestId) return null
        this.dreamRuns = data.items
        return data
      } finally {
        if (requestId === this.dreamRunsRequestId) this.dreamRunsLoading = false
      }
    },
    async startDreamRun(sessionId = null) {
      this.dreamRunStarting = true
      try {
        const { data } = await api.post('/memories/dream-runs', sessionId ? { sessionId } : {})
        await this.fetchDreamRuns()
        return data
      } finally {
        this.dreamRunStarting = false
      }
    }
  }
})
