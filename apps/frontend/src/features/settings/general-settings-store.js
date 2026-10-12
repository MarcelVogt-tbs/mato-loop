import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useAuthStore } from 'stores/auth'

export const useGeneralSettingsStore = defineStore('general-settings', {
  state: () => ({ publicBaseUrl: null, effectivePublicBaseUrl: '', publicUrlSource: 'automatic', modelTrustEnabled: true, loading: false, loaded: false, loadFailed: false, saving: false }),
  actions: {
    async fetchSettings() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/general-settings')
        Object.assign(this, data, { loaded: true })
        useAuthStore().modelTrustEnabled = this.modelTrustEnabled
        return data
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally { this.loading = false }
    },
    async saveSettings(values) {
      this.saving = true
      try {
        const { data } = await api.put('/general-settings', { ...values, ...(Object.hasOwn(values, 'publicBaseUrl') ? { publicBaseUrl: values.publicBaseUrl || null } : {}) })
        Object.assign(this, data)
        useAuthStore().modelTrustEnabled = this.modelTrustEnabled
        return data
      } finally { this.saving = false }
    }
  }
})
