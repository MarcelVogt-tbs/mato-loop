import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const defaultIdentity = () => ({
  displayName: 'MatoLoop',
  instructions: ''
})

export const useAgentIdentityStore = defineStore('agent-identity', {
  state: () => ({
    ...defaultIdentity(),
    loaded: false,
    loadFailed: false,
    loading: false,
    saving: false,
    requestRevision: 0
  }),
  actions: {
    resetIdentity() {
      this.requestRevision += 1
      const fallback = defaultIdentity()
      this.displayName = fallback.displayName
      this.instructions = fallback.instructions
      this.loaded = false
      this.loadFailed = false
      this.loading = false
      this.saving = false
    },
    assignIdentity(identity, invalidateRequests = true) {
      if (invalidateRequests) this.requestRevision += 1
      const fallback = defaultIdentity()
      this.displayName = String(identity?.displayName || fallback.displayName)
      this.instructions = typeof identity?.instructions === 'string'
        ? identity.instructions
        : fallback.instructions
      this.loaded = true
      this.loadFailed = false
    },
    async fetchIdentity(options) {
      const revision = ++this.requestRevision
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/agent-identity', options)
        if (revision === this.requestRevision) this.assignIdentity(data, false)
        return { displayName: this.displayName, instructions: this.instructions }
      } catch (error) {
        if (revision === this.requestRevision) this.loadFailed = true
        throw error
      } finally {
        if (revision === this.requestRevision) this.loading = false
      }
    },
    async saveIdentity(identity) {
      const revision = ++this.requestRevision
      this.saving = true
      try {
        const { data } = await api.patch('/agent-identity', {
          display_name: identity.displayName,
          instructions: identity.instructions
        })
        if (revision === this.requestRevision) this.assignIdentity(data, false)
        return { displayName: this.displayName, instructions: this.instructions }
      } finally {
        if (revision === this.requestRevision) this.saving = false
      }
    }
  }
})
