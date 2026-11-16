import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useUserPreferencesStore = defineStore('user-preferences', {
  state: () => ({ preferredLanguage: null, sessionsPosition: 'belowProjects', loading: false, loaded: false, loadFailed: false, saving: false }),
  actions: {
    async fetchSettings() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/users/me/preferences')
        this.preferredLanguage = data.preferredLanguage
        this.sessionsPosition = data.sessionsPosition ?? 'belowProjects'
        this.loaded = true
        return data
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally { this.loading = false }
    },
    async saveSettings({ preferredLanguage, sessionsPosition }) {
      this.saving = true
      try {
        const { data } = await api.put('/users/me/preferences', { preferredLanguage, sessionsPosition })
        this.preferredLanguage = data.preferredLanguage
        this.sessionsPosition = data.sessionsPosition ?? 'belowProjects'
        return data
      } finally { this.saving = false }
    }
  }
})
