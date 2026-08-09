import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { errorDescriptor } from 'src/utils/api-errors'

const TERMINAL_DECISION_ERRORS = new Set([
  'approval.runInactive',
  'approval.alreadyDecided',
  'approval.notFound'
])

export const useApprovalStore = defineStore('approvals', {
  state: () => ({ pending: [], submittingId: null }),
  getters: {
    forSession: (state) => (sessionId) => state.pending.filter((item) => item.sessionId === sessionId)
  },
  actions: {
    async fetchPending() {
      this.pending = (await api.get('/approvals/pending')).data
    },
    add(approval) {
      if (!this.pending.some((item) => item.id === approval.id)) this.pending.push(approval)
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
        this.pending = this.pending.filter((item) => item.id !== id)
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
