import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    statusLoaded: false,
    setupRequired: false,
    authenticated: false,
    username: null,
    preferences: { thinkingExpanded: false },
    confirmedThinkingExpanded: false,
    preferenceRevision: 0
  }),
  actions: {
    async fetchStatus() {
      const { data } = await api.get('/auth/status')
      this.statusLoaded = true
      this.setupRequired = data.setup_required
      this.authenticated = data.authenticated
      this.username = data.username
      if (data.authenticated) await this.fetchPreferences()
      return data
    },
    async fetchPreferences() {
      this.preferences = (await api.get('/auth/preferences')).data
      this.confirmedThinkingExpanded = this.preferences.thinkingExpanded
    },
    async setup(credentials) {
      const { data } = await api.post('/auth/setup', credentials)
      this.setupRequired = false
      this.authenticated = true
      this.username = data.username
      await this.fetchPreferences()
    },
    async login(credentials) {
      const { data } = await api.post('/auth/login', credentials)
      this.authenticated = true
      this.username = data.username
      await this.fetchPreferences()
    },
    async logout() {
      await api.post('/auth/logout')
      this.authenticated = false
      this.username = null
      this.preferences = { thinkingExpanded: false }
      this.confirmedThinkingExpanded = false
    },
    async setThinkingExpanded(expanded) {
      const revision = ++this.preferenceRevision
      this.preferences.thinkingExpanded = expanded
      try {
        const { data } = await api.patch('/auth/preferences', {
          thinking_expanded: expanded
        })
        if (revision === this.preferenceRevision) {
          this.preferences = data
          this.confirmedThinkingExpanded = data.thinkingExpanded
        }
      } catch (error) {
        if (revision === this.preferenceRevision) {
          try {
            await this.fetchPreferences()
          } catch {
            this.preferences.thinkingExpanded = this.confirmedThinkingExpanded
          }
        }
        throw error
      }
    }
  }
})
