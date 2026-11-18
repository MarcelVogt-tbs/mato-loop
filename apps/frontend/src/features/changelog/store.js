import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useChangelogStore = defineStore('changelog', {
  state: () => ({
    open: false, comparison: null, channel: 'stable', result: null,
    loading: false, failed: false, requestId: 0
  }),
  actions: {
    show(comparison = null) {
      this.comparison = comparison
      this.open = true
      return this.fetchChanges()
    },
    async fetchChanges() {
      const requestId = ++this.requestId
      this.result = null
      this.failed = false
      this.loading = true
      try {
        const params = this.comparison
          ? { base_revision: this.comparison.base, target_revision: this.comparison.target }
          : { channel: this.channel }
        const { data } = await api.get('/changelog', { params })
        if (requestId === this.requestId) this.result = data
      } catch {
        // The central interceptor displays API errors.
        if (requestId === this.requestId) this.failed = true
      } finally {
        if (requestId === this.requestId) this.loading = false
      }
    }
  }
})
