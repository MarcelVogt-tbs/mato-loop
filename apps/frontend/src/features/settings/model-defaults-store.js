import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useModelDefaultsStore = defineStore('model-defaults', {
  state: () => ({ configured: {}, inherited: {}, effective: {}, profiles: [], automaticModelId: null, personalSettingsEnabled: true, loading: false, saving: false, loadVersion: 0 }),
  actions: {
    async load(scope) {
      const version = ++this.loadVersion
      this.loading = true
      try {
        const { data } = await api.get('/model-defaults', { params: { scope } })
        if (version === this.loadVersion) Object.assign(this, data)
      } finally { if (version === this.loadVersion) this.loading = false }
    },
    async save(scope, payload) {
      this.saving = true
      try { Object.assign(this, (await api.put('/model-defaults', payload, { params: { scope } })).data) }
      finally { this.saving = false }
    },
    async setPersonalEnabled(enabled) {
      this.saving = true
      try { Object.assign(this, (await api.put('/model-defaults/personal-enabled', { enabled })).data) }
      finally { this.saving = false }
    }
  }
})
