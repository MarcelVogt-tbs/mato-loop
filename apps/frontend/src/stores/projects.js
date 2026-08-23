import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useProjectStore = defineStore('projects', {
  state: () => ({
    items: [],
    loading: false,
    loaded: false,
    loadFailed: false,
    revision: 0
  }),
  actions: {
    async fetchAll() {
      const revision = ++this.revision
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/projects')
        if (revision !== this.revision) return
        this.items = data.items
        this.loaded = true
      } catch (error) {
        if (revision === this.revision) this.loadFailed = true
        throw error
      } finally {
        if (revision === this.revision) this.loading = false
      }
    },
    async create(name) {
      const { data } = await api.post('/projects', { name })
      this.revision += 1
      this.loading = false
      this.items = [...this.items, data]
      return data
    },
    async update(id, name) {
      const { data } = await api.patch(`/projects/${id}`, { name })
      this.revision += 1
      this.loading = false
      this.items = this.items.map((project) => project.id === id ? data : project)
      return data
    },
    async remove(id, { automationMode = null, automationImpact, suppressErrorToast = false } = {}) {
      const config = {
        ...(suppressErrorToast ? { suppressErrorToast: true } : {}),
        params: {
          ...(automationMode ? { automationMode } : {}),
          automationImpact
        }
      }
      await api.delete(`/projects/${id}`, config)
      this.revision += 1
      this.loading = false
      this.items = this.items.filter((project) => project.id !== id)
    },
    async deleteImpact(id, { suppressErrorToast = false } = {}) {
      const { data } = await api.get(
        `/projects/${id}/delete-impact`,
        suppressErrorToast ? { suppressErrorToast: true } : undefined
      )
      return data
    }
  }
})
