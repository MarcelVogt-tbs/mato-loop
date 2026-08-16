import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { errorDescriptor } from 'src/utils/api-errors'

const TERMINAL_DECISION_ERRORS = new Set([
  'approval.policyChanged',
  'approval.runInactive',
  'approval.targetChanged',
  'approval.alreadyDecided',
  'approval.notFound'
])

export const useApprovalStore = defineStore('approvals', {
  state: () => ({
    pending: [],
    submittingId: null,
    loading: false,
    loaded: false,
    loadFailed: false,
    revision: 0
  }),
  getters: {
    pendingCount: (state) => state.pending.length,
    forSession: (state) => (sessionId) => state.pending.filter((item) => (
      item.sessionId === sessionId && item.requestType !== 'memory_change'
    ))
  },
  actions: {
    async fetchPending(config = {}) {
      const revision = ++this.revision
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = Object.keys(config).length
          ? await api.get('/approvals/pending', config)
          : await api.get('/approvals/pending')
        if (revision !== this.revision || !Array.isArray(data)) return
        this.pending = data
        this.loaded = true
      } catch (error) {
        if (revision === this.revision) this.loadFailed = true
        throw error
      } finally {
        if (revision === this.revision) this.loading = false
      }
    },
    add(approval) {
      if (this.pending.some((item) => item.id === approval.id)) return
      this.revision += 1
      this.pending.push(approval)
      this.loading = false
      this.loaded = true
      this.loadFailed = false
    },
    async decide(id, decision, expectedRevision) {
      this.submittingId = id
      try {
        await api.post(`/approvals/${id}/decisions`, {
          decision,
          ...(decision.startsWith('allow') && expectedRevision != null
            ? { expected_revision: expectedRevision }
            : {})
        })
        this.revision += 1
        this.pending = this.pending.filter((item) => item.id !== id)
        this.loading = false
      } catch (error) {
        if (TERMINAL_DECISION_ERRORS.has(errorDescriptor(error)?.code)) {
          await this.fetchPending()
        }
        throw error
      } finally {
        this.submittingId = null
      }
    },
    async permissions(sessionId, config = {}) {
      return (await api.get(`/approvals/sessions/${sessionId}/permissions`, config)).data
    },
    async revoke(sessionId, grantId) {
      await api.delete(`/approvals/sessions/${sessionId}/permissions/${grantId}`)
    }
  }
})
