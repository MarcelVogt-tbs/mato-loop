import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useChangelogStore = defineStore('changelog', {
  state: () => ({
    open: false, comparison: null, tab: 'installed', channel: 'develop', result: null,
    loading: false, failed: false, requestId: 0
  }),
  actions: {
    show(comparison = null) {
      this.comparison = comparison
      this.tab = 'installed'
      this.channel = 'develop'
      this.open = true
      return this.fetchChanges()
    },
    selectTab(tab) {
      if (tab === this.tab) return
      this.tab = tab
      return this.fetchChanges()
    },
    async fetchChanges() {
      const requestId = ++this.requestId
      this.result = null
      this.failed = false
      this.loading = true
      try {
        const { data } = this.comparison
          ? await api.get('/changelog', { params: {
            base_revision: this.comparison.base, target_revision: this.comparison.target
          } })
          : this.tab === 'available'
            ? await api.get('/changelog', { params: { channel: this.channel } })
            : await api.get('/changelog/installed')
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
