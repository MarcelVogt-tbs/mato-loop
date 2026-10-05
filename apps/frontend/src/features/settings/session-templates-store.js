import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useSessionTemplateStore = defineStore('session-templates', {
  state: () => ({ templates: [], personalSettingsEnabled: true, defaultTemplateId: null, loading: false, saving: false, loadVersion: 0 }),
  actions: {
    async load(scope = null) {
      const version = ++this.loadVersion
      this.loading = true
      try {
        const { data } = await api.get('/session-templates', { params: { scope } })
        if (version === this.loadVersion) Object.assign(this, data)
      } finally { if (version === this.loadVersion) this.loading = false }
    },
    async save(scope, value) {
      this.saving = true
      try { Object.assign(this, (await api.put(`/session-templates/${value.id}`, value, { params: { scope } })).data) }
      finally { this.saving = false }
    },
    async remove(scope, id) {
      this.saving = true
      try { Object.assign(this, (await api.delete(`/session-templates/${id}`, { params: { scope } })).data) }
      finally { this.saving = false }
    },
    async setPersonalEnabled(enabled) {
      this.saving = true
      try { Object.assign(this, (await api.put('/session-templates/personal-enabled', { enabled })).data) }
      finally { this.saving = false }
    }
  }
})
