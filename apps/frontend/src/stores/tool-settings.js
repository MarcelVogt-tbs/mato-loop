import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const defaultWebFetch = () => ({
  enabled: true,
  defaultPolicy: 'ask',
  allowedPrivateOrigins: []
})

export const useToolSettingsStore = defineStore('tool-settings', {
  state: () => ({
    webFetch: defaultWebFetch(),
    loaded: false,
    loadFailed: false,
    loading: false,
    saving: false
  }),
  actions: {
    async fetchSettings() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/tools/settings')
        this.webFetch = { ...defaultWebFetch(), ...data.webFetch }
        this.loaded = true
        return this.webFetch
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async saveSettings(webFetch) {
      this.saving = true
      try {
        const { data } = await api.put('/tools/settings', { webFetch })
        this.webFetch = { ...defaultWebFetch(), ...data.webFetch }
        this.loaded = true
        return this.webFetch
      } finally {
        this.saving = false
      }
    }
  }
})
