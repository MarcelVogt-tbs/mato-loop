import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useHomeStore = defineStore('home', {
  state: () => ({ sessions: [], loading: false, loaded: false, loadFailed: false }),
  actions: {
    async load(config = {}) {
      if (this.loading) return
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/sessions', { ...config, params: { pageSize: 6, sort: 'recent' } })
        this.sessions = data.items
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    }
  }
})
