import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { api } from 'src/boot/axios'
import { queueRefresh } from 'src/utils/queued-refresh'

export const useHomeStore = defineStore('home', {
  state: () => ({ sessions: [], displayed: false, visitReady: false, visit: 0, loading: false, loaded: false, stale: false, loadFailed: false, loadRequest: null, revision: 0 }),
  actions: {
    async beginVisit() {
      const visit = ++this.visit
      this.displayed = true
      this.visitReady = false
      await this.load()
      while (this.loadRequest) await this.loadRequest
      if (this.displayed && this.visit === visit) this.visitReady = true
    },
    endVisit() {
      this.displayed = false
      this.visitReady = false
      this.visit += 1
    },
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
          const retainOrder = this.displayed && this.visitReady
          const retainedIds = retainOrder ? this.sessions.map((session) => session.id) : []
          const { data } = await api.post('/sessions/home', { retainedIds }, config)
          if (revision !== this.revision) return
          const incoming = new Map(data.items.map((session) => [session.id, session]))
          const retained = new Set(retainedIds)
          this.sessions = retainOrder
            ? [...retainedIds.filter((id) => incoming.has(id)), ...data.items.map((session) => session.id).filter((id) => !retained.has(id))].map((id) => incoming.get(id))
            : data.items
          this.loaded = true
          this.stale = false
          if (this.displayed) this.visitReady = true
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
