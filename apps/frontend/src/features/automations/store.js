import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useAutomationStore = defineStore('automations', {
  state: () => ({
    items: [],
    sessionItems: [],
    sessionItemsRevision: 0,
    loading: false,
    loaded: false,
    loadFailed: false,
    dirty: false,
    listRevision: 0
  }),
  actions: {
    async fetchForSession(sessionId) {
      const revision = ++this.sessionItemsRevision
      const { data } = await api.get('/automations', { params: { sessionId } })
      if (revision === this.sessionItemsRevision) this.sessionItems = data.items
      return data.items
    },
    async fetchAutomations({ suppressErrorToast = false } = {}) {
      const revision = ++this.listRevision
      this.loading = true
      const params = { summary: true }
      try {
        const { data } = await (suppressErrorToast
          ? api.get('/automations', { params, suppressErrorToast: true })
          : api.get('/automations', { params }))
        if (revision === this.listRevision) {
          this.items = data.items
          this.loaded = true
          this.loadFailed = false
        }
        return data
      } catch (error) {
        if (revision === this.listRevision) this.loadFailed = true
        throw error
      } finally {
        if (revision === this.listRevision) this.loading = false
      }
    },
    async fetchAutomation(id, { suppressErrorToast = false } = {}) {
      const { data } = await (suppressErrorToast
        ? api.get(`/automations/${id}`, { params: { includeHistory: false }, suppressErrorToast: true })
        : api.get(`/automations/${id}`, { params: { includeHistory: false } }))
      return data
    },
    async createAutomation(payload) {
      const { data } = await api.post('/automations', payload)
      this.listRevision += 1
      this.loading = false
      this.items = [data, ...this.items.filter((item) => item.id !== data.id)]
      this.sessionItemsRevision += 1
      this.sessionItems = [data, ...this.sessionItems.filter((item) => item.id !== data.id)]
      this.loaded = true
      return data
    },
    async updateAutomation(id, payload) {
      const { data } = await api.put(`/automations/${id}`, payload)
      this.listRevision += 1
      this.loading = false
      const index = this.items.findIndex((item) => item.id === id)
      if (index >= 0) this.items[index] = data
      this.sessionItemsRevision += 1
      this.sessionItems = this.sessionItems.map((item) => item.id === id ? data : item)
      return data
    },
    async deleteAutomation(id, expectedRevision) {
      await api.delete(`/automations/${id}`, { params: { expectedRevision } })
      this.listRevision += 1
      this.loading = false
      this.items = this.items.filter((item) => item.id !== id)
      this.sessionItemsRevision += 1
      this.sessionItems = this.sessionItems.filter((item) => item.id !== id)
    },
    async fetchRun(automationId, runId, { suppressErrorToast = false } = {}) {
      const url = `/automations/${automationId}/runs/${runId}`
      const { data } = await (suppressErrorToast
        ? api.get(url, { suppressErrorToast: true })
        : api.get(url))
      return data
    },
    async runNow(id, expectedRevision) {
      const { data } = await api.post(`/automations/${id}/runs`, { expectedRevision })
      this.listRevision += 1
      this.loading = false
      const update = (automation) => automation?.id === id
        ? { ...automation, effectiveState: { ...automation.effectiveState, activeRunStatus: data.status } }
        : automation
      this.items = this.items.map(update)
      return data
    }
  }
})
