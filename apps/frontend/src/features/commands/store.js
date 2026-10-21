import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useCommandStore = defineStore('commands', {
  state: () => ({ items: [], loaded: false, loading: false, failed: false, catalogKey: null, contextKey: null, version: 0 }),
  actions: {
    async load(context, key = JSON.stringify(context), force = false) {
      if (key === this.catalogKey && (this.loading || (this.loaded && !force))) return
      const version = ++this.version
      const contextKey = JSON.stringify(context)
      if (contextKey !== this.contextKey) { this.items = []; this.loaded = false }
      this.contextKey = contextKey
      this.catalogKey = key
      this.loading = true
      this.failed = false
      try {
        const { data } = await api.post('/commands/catalog', context, { suppressErrorToast: true })
        if (version !== this.version) return
        if (!Array.isArray(data.items)) throw new Error('Invalid command catalog')
        if (JSON.stringify(this.items) !== JSON.stringify(data.items)) this.items = data.items
        this.loaded = true
      } catch {
        if (version === this.version) this.failed = true
      } finally {
        if (version === this.version) this.loading = false
      }
    }
  }
})
