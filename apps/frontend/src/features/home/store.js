import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { api } from 'src/boot/axios'
import { queueRefresh } from 'src/utils/queued-refresh'

export const useHomeStore = defineStore('home', {
  state: () => ({ sessions: [], loading: false, loaded: false, loadFailed: false, loadRequest: null, revision: 0 }),
  actions: {
    ensureLoaded() {
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
