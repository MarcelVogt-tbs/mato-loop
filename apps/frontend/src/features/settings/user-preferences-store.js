import { defineStore } from 'pinia'
import { markRaw } from 'vue'
import { api } from 'src/boot/axios'

export const useUserPreferencesStore = defineStore('user-preferences', {
  state: () => ({ preferredLanguage: null, sessionsPosition: 'belowProjects', sessionsPageSize: 10, sessionTabsEnabled: false, sessionTabsPosition: 'bottom', trashEnabled: false, trashRetentionDays: 30, loading: false, loaded: false, loadFailed: false, saving: false, loadRequest: null }),
  actions: {
    applySettings(data) {
      this.preferredLanguage = data.preferredLanguage
      this.sessionsPosition = data.sessionsPosition ?? 'belowProjects'
      this.sessionsPageSize = data.sessionsPageSize ?? 10
      this.sessionTabsEnabled = data.sessionTabsEnabled ?? false
      this.sessionTabsPosition = data.sessionTabsPosition ?? 'bottom'
      this.trashEnabled = data.trashEnabled ?? false
      this.trashRetentionDays = data.trashRetentionDays ?? 30
    },
    async fetchSettings() {
      if (this.loadRequest) return this.loadRequest
      this.loading = true
      this.loadFailed = false
      const request = api.get('/users/me/preferences').then(({ data }) => {
        this.applySettings(data)
        this.loaded = true
        return data
      })
      this.loadRequest = markRaw(request)
      try {
        return await request
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
        this.loadRequest = null
      }
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
