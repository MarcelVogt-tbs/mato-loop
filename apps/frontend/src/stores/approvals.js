import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { errorDescriptor } from 'src/utils/api-errors'
import { validRelativePath } from 'src/utils/attachments'

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
    revision: 0,
    grants: [],
    grantsLoading: false,
    grantsLoaded: false,
    grantsLoadFailed: false,
    grantRevision: 0
  }),
  getters: {
    pendingCount: (state) => state.pending.length,
    grantsForSession: (state) => (sessionId) => state.grants.filter((item) => item.sessionId === sessionId),
    forSession: (state) => (sessionId) => state.pending.filter((item) => (
      item.sessionId === sessionId && (item.requestType !== 'memory_change' || item.payload?.subagentSessionId)
    ))
  },
  actions: {
    async previewSkillDraft(approval) {
      const sessionId = approval?.payload?.subagentSessionId
      const draft = approval?.details?.draftFile
      if (!sessionId || !['build_skill', 'edit_skill'].includes(approval?.actionKind)
        || draft?.root !== 'session' || !validRelativePath(draft.path)) return null
      // An omitted root is resolved by the backend from the child, not the active parent.
      return (await api.get('/workspace/files/preview', {
        params: {
          scope: 'session', session_id: sessionId, path: draft.path,
          ...(draft.rootIdentity ? { root_identity: draft.rootIdentity } : {})
        }
      })).data
    },
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
        if (decision === 'allow_session' && this.grantsLoaded) {
          void this.fetchGrants({ suppressErrorToast: true }).catch(() => {})
        }
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
    async fetchGrants(config = {}) {
      const revision = ++this.grantRevision
      this.grantsLoading = true
      this.grantsLoadFailed = false
      try {
        const { data } = Object.keys(config).length
          ? await api.get('/approvals/permissions', config)
          : await api.get('/approvals/permissions')
        if (revision !== this.grantRevision || !Array.isArray(data)) return
        this.grants = data
        this.grantsLoaded = true
      } catch (error) {
        if (revision === this.grantRevision) this.grantsLoadFailed = true
        throw error
      } finally {
        if (revision === this.grantRevision) this.grantsLoading = false
      }
    },
    async revoke(sessionId, grantId) {
      await api.delete(`/approvals/sessions/${sessionId}/permissions/${grantId}`)
      this.grantRevision += 1
      this.grants = this.grants.filter((item) => item.id !== grantId)
      this.grantsLoading = false
    },
    async revokeAll(sessionId) {
      await api.delete(`/approvals/sessions/${sessionId}/permissions`)
      this.grantRevision += 1
      this.grants = this.grants.filter((item) => item.sessionId !== sessionId)
      this.grantsLoading = false
    }
  }
})
