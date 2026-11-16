import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useUserPreferencesStore = defineStore('user-preferences', {
  state: () => ({ preferredLanguage: null, sessionsPosition: 'belowProjects', trashEnabled: false, trashRetentionDays: 30, loading: false, loaded: false, loadFailed: false, saving: false }),
  actions: {
    applySettings(data) {
      this.preferredLanguage = data.preferredLanguage
      this.sessionsPosition = data.sessionsPosition ?? 'belowProjects'
      this.trashEnabled = data.trashEnabled ?? false
      this.trashRetentionDays = data.trashRetentionDays ?? 30
    },
    async fetchSettings() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/users/me/preferences')
        this.applySettings(data)
        this.loaded = true
        return data
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally { this.loading = false }
    },
    async saveSettings(preferences) {
      this.saving = true
      try {
        const { data } = await api.put('/users/me/preferences', preferences)
        this.applySettings(data)
        return data
      } finally { this.saving = false }
    }
  }
})
