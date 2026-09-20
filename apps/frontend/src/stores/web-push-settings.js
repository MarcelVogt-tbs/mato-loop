import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useWebPushSettingsStore = defineStore('web-push-settings', {
  state: () => ({
    enabled: false,
    configured: false,
    contactEmail: null,
    loaded: false,
    loading: false,
    loadFailed: false,
    saving: false
  }),
  actions: {
    assign(data) {
      this.enabled = Boolean(data.enabled)
      this.configured = Boolean(data.configured)
      this.contactEmail = data.contactEmail || null
      this.loaded = true
    },
    async fetchSettings() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/notifications/push/settings')
        this.assign(data)
        return data
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async saveSettings(payload) {
      this.saving = true
      try {
        const { data } = await api.put('/notifications/push/settings', payload)
        this.assign(data)
        return data
      } finally {
        this.saving = false
      }
    }
  }
})
