import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useWorkspaceStore = defineStore('workspace', {
  state: () => ({ open: false, entries: {}, loading: {}, selectedPath: '.', filter: '' }),
  actions: {
    show() {
      this.open = true
      this.load('.')
    },
    async load(path = '.') {
      this.loading[path] = true
      try {
        this.entries[path] = (await api.get('/workspace/entries', { params: { path } })).data.entries
      } finally {
        this.loading[path] = false
      }
    },
    async createDirectory(parent, name) {
      await api.post('/workspace/directories', { parent, name })
      await this.load(parent)
    },
    async deleteEntry(path) {
      await api.delete('/workspace/entries', { params: { path } })
      const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '.'
      await this.load(parent || '.')
    }
  }
})
