import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useTbsDeskStore = defineStore('tbs-desk', {
  state: () => ({
    sessionId: null,
    state: null,
    detail: null,
    loading: false,
    loadFailed: false,
    detailLoading: false,
    requestVersion: 0,
    detailVersion: 0
  }),
  actions: {
    async fetchSession(sessionId) {
      const version = ++this.requestVersion
      if (this.sessionId !== sessionId) {
        this.sessionId = sessionId
        this.state = null
        this.clearDetail()
      }
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get(`/tbs-desk/sessions/${sessionId}`)
        if (version !== this.requestVersion || this.sessionId !== sessionId) return
        this.state = data
        return data
      } catch (error) {
        if (version === this.requestVersion) this.loadFailed = true
        throw error
      } finally {
        if (version === this.requestVersion) this.loading = false
      }
    },
    async clearCustomer(sessionId) {
      const version = ++this.requestVersion
      this.loading = true
      try {
        const { data } = await api.post(`/tbs-desk/sessions/${sessionId}/clear-customer`)
        if (version !== this.requestVersion || this.sessionId !== sessionId) return
        this.state = data
        this.clearDetail()
        return data
      } finally {
        if (version === this.requestVersion) this.loading = false
      }
    },
    async fetchTicket(sessionId, ticketId) {
      const version = ++this.detailVersion
      this.detailLoading = true
      try {
        const { data } = await api.get(`/tbs-desk/sessions/${sessionId}/tickets/${encodeURIComponent(ticketId)}`)
        if (version !== this.detailVersion || this.sessionId !== sessionId) return
        this.detail = data
        return data
      } finally {
        if (version === this.detailVersion) this.detailLoading = false
      }
    },
    clearDetail() {
      this.detailVersion += 1
      this.detailLoading = false
      this.detail = null
    }
  }
})
