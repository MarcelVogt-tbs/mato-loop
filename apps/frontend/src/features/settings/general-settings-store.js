import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useGeneralSettingsStore = defineStore('general-settings', {
  state: () => ({ publicBaseUrl: null, effectivePublicBaseUrl: '', publicUrlSource: 'automatic', loading: false, loaded: false, loadFailed: false, saving: false }),
  actions: {
    async fetchSettings() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/general-settings')
        Object.assign(this, data, { loaded: true })
        return data
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally { this.loading = false }
    },
    async saveSettings(publicBaseUrl) {
      this.saving = true
      try {
        const { data } = await api.put('/general-settings', { publicBaseUrl: publicBaseUrl || null })
        Object.assign(this, data)
        return data
      } finally { this.saving = false }
    }
  }
})
