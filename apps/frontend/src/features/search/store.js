import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useSearchStore = defineStore('search', {
  state: () => ({
    dialogOpen: false,
    query: '',
    searchedQuery: '',
    projects: [],
    sessions: [],
    strategy: null,
    loading: false,
    loadFailed: false,
    revision: 0
  }),
  actions: {
    openDialog() {
      this.dialogOpen = true
    },
    clearResults() {
      this.revision += 1
      this.searchedQuery = ''
      this.projects = []
      this.sessions = []
      this.strategy = null
      this.loading = false
      this.loadFailed = false
    },
    async run() {
      const query = String(this.query || '').trim()
      if (query.length < 2) return
      const revision = ++this.revision
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/search', { params: { q: query } })
        if (revision !== this.revision) return
        this.searchedQuery = query
        this.projects = data.projects
        this.sessions = data.sessions
        this.strategy = data.strategy
      } catch (error) {
        if (revision === this.revision) this.loadFailed = true
        throw error
      } finally {
        if (revision === this.revision) this.loading = false
      }
    }
  }
})
