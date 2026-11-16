import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { api } from 'src/boot/axios'
import { queueRefresh } from 'src/utils/queued-refresh'

export const useHomeStore = defineStore('home', {
  state: () => ({ sessions: [], displayed: false, loading: false, loaded: false, stale: false, loadFailed: false, loadRequest: null, revision: 0 }),
  actions: {
    invalidate() {
      this.stale = true
      this.revision += 1
      this.loading = false
    },
    ensureLoaded() {
      if (this.stale) return this.load()
      if (this.loadRequest) return this.loadRequest
      if (!this.loaded) return this.load()
    },
    async load(config = {}) {
      const request = queueRefresh(this, config, async (revision) => {
        this.loading = true
        this.loadFailed = false
        try {
          const { data } = await api.get('/sessions', { ...config, params: { pageSize: 6, sort: 'recent' } })
          if (revision !== this.revision) return
          this.sessions = data.items
          this.loaded = true
          this.stale = false
        } catch (error) {
          if (revision === this.revision) this.loadFailed = true
          throw error
        } finally {
          if (revision === this.revision) this.loading = false
        }
      })
      this.loadRequest = markRaw(request)
      try {
        await request
      } finally {
        if (this.loadRequest === request) this.loadRequest = null
      }
    }
  }
})
