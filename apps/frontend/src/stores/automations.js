import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const PAGE_SIZE = 20

export const useAutomationStore = defineStore('automations', {
  state: () => ({
    items: [],
    detail: null,
    status: null,
    statusRevision: 0,
    runs: [],
    selectedRun: null,
    page: 0,
    hasMore: false,
    loading: false,
    loadingDetail: false,
    loadingRuns: false,
    listArchived: false,
    listRevision: 0,
    detailRevision: 0,
    runsRevision: 0,
    runDetailRevision: 0
  }),
  actions: {
    async fetchAutomations({ archived = false, suppressErrorToast = false } = {}) {
      const revision = ++this.listRevision
      this.listArchived = archived
      this.loading = true
      try {
        const { data } = await (suppressErrorToast
          ? api.get('/automations', { params: { archived, summary: true }, suppressErrorToast: true })
          : api.get('/automations', { params: { archived, summary: true } }))
        if (revision === this.listRevision) this.items = data.items
        return data
      } finally {
        if (revision === this.listRevision) this.loading = false
      }
    },
    async fetchAutomation(id, { suppressErrorToast = false } = {}) {
      const revision = ++this.detailRevision
      this.loadingDetail = true
      try {
        const { data } = await (suppressErrorToast
          ? api.get(`/automations/${id}`, { params: { includeHistory: false }, suppressErrorToast: true })
          : api.get(`/automations/${id}`, { params: { includeHistory: false } }))
        if (revision === this.detailRevision) this.detail = data
        return data
      } finally {
        if (revision === this.detailRevision) this.loadingDetail = false
      }
    },
    async fetchAutomationStatus(id, { suppressErrorToast = false } = {}) {
      const revision = ++this.statusRevision
      const { data } = await api.get(`/automations/${id}/status`, {
        ...(suppressErrorToast ? { suppressErrorToast: true } : {})
      })
      if (revision === this.statusRevision) this.status = data
      return data
    },
    async createAutomation(payload) {
      const { data } = await api.post('/automations', payload)
      this.listRevision += 1
      this.detailRevision += 1
      this.loading = false
      this.loadingDetail = false
      this.items = [data, ...this.items.filter((item) => item.id !== data.id)]
      this.detail = data
      this.statusRevision += 1
      this.status = null
      return data
    },
    async updateAutomation(id, payload) {
      const { data } = await api.put(`/automations/${id}`, payload)
      this.listRevision += 1
      this.detailRevision += 1
      this.loading = false
      this.loadingDetail = false
      const index = this.items.findIndex((item) => item.id === id)
      if (index >= 0) this.items[index] = data
      this.detail = data
      this.statusRevision += 1
      this.status = null
      return data
    },
    async deleteAutomation(id, expectedRevision) {
      await api.delete(`/automations/${id}`, { params: { expectedRevision } })
      this.listRevision += 1
      this.detailRevision += 1
      this.loading = false
      this.loadingDetail = false
      this.items = this.items.filter((item) => item.id !== id)
      if (this.detail?.id === id) this.detail = null
      if (this.status?.id === id) {
        this.statusRevision += 1
        this.status = null
      }
    },
    async fetchRuns(id, page = 1, { suppressErrorToast = false, preserveLoaded = false } = {}) {
      const revision = page === 1 ? ++this.runsRevision : this.runsRevision
      this.loadingRuns = true
      try {
        const { data } = await api.get(`/automations/${id}/runs`, {
          params: { page, pageSize: PAGE_SIZE, summary: true },
          ...(suppressErrorToast ? { suppressErrorToast: true } : {})
        })
        if (revision !== this.runsRevision) return data
        this.runs = page === 1
          ? preserveLoaded
            ? [...data.items, ...this.runs.filter((run) => !data.items.some((item) => item.id === run.id))]
            : data.items
          : [...this.runs, ...data.items.filter((run) => !this.runs.some((item) => item.id === run.id))]
        this.page = preserveLoaded ? Math.max(this.page, data.page) : data.page
        if (!preserveLoaded || this.page === 1) this.hasMore = data.hasMore
        return data
      } finally {
        if (revision === this.runsRevision) this.loadingRuns = false
      }
    },
    async fetchRun(automationId, runId, { suppressErrorToast = false } = {}) {
      const revision = ++this.runDetailRevision
      const url = `/automations/${automationId}/runs/${runId}`
      const { data } = await (suppressErrorToast
        ? api.get(url, { suppressErrorToast: true })
        : api.get(url))
      if (revision === this.runDetailRevision) this.selectedRun = data
      return data
    },
    async runNow(id, expectedRevision) {
      const { data } = await api.post(`/automations/${id}/runs`, { expectedRevision })
      this.listRevision += 1
      this.runsRevision += 1
      this.runDetailRevision += 1
      this.loading = false
      this.loadingRuns = false
      this.runs = [data, ...this.runs.filter((run) => run.id !== data.id)]
      this.selectedRun = data
      const update = (automation) => automation?.id === id
        ? { ...automation, effectiveState: { ...automation.effectiveState, activeRunStatus: data.status } }
        : automation
      this.items = this.items.map(update)
      this.detail = update(this.detail)
      return data
    },
    async retryDelivery(automationId, runId) {
      const { data } = await api.post(`/automations/${automationId}/runs/${runId}/delivery-retry`)
      this.runsRevision += 1
      this.runDetailRevision += 1
      this.loadingRuns = false
      this.runs = this.runs.map((run) => run.id === runId ? data : run)
      if (this.selectedRun?.id === runId) this.selectedRun = data
      return data
    },
    clearDetail() {
      this.statusRevision += 1
      this.status = null
      this.detailRevision += 1
      this.runsRevision += 1
      this.runDetailRevision += 1
      this.detail = null
      this.runs = []
      this.selectedRun = null
      this.page = 0
      this.hasMore = false
      this.loadingDetail = false
      this.loadingRuns = false
    }
  }
})
