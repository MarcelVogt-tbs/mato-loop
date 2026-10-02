import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useArchiveStore = defineStore('archive', {
  state: () => ({ projects: [], sessionLists: {}, loading: false, failed: false }),
  actions: {
    async load() {
      this.loading = true
      this.failed = false
      try {
        const [{ data }] = await Promise.all([api.get('/projects', { params: { archived: true } }), this.loadSessions()])
        this.projects = data.items
      } catch {
        this.failed = true
      } finally { this.loading = false }
    },
    async loadSessions(projectId = null, more = false) {
      const key = projectId || 'sessions'
      const list = this.sessionLists[key] || { items: [], nextCursor: null }
      const { data } = await api.get('/sessions', { params: {
        archived: true, pageSize: 20, ...(projectId ? { projectId } : {}),
        ...(more && list.nextCursor ? { cursor: list.nextCursor } : {})
      } })
      this.sessionLists[key] = { items: more ? [...list.items, ...data.items] : data.items, nextCursor: data.nextCursor }
    }
  }
})
