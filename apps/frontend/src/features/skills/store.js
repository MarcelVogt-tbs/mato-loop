import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const scopeConfig = (scope) => ({ params: { scope } })

export const useSkillStore = defineStore('skills', {
  state: () => ({ scopedItems: [], scope: 'personal', loading: false, saving: false, personalSkillsEnabled: true, settingsLoaded: false, draftReviews: {} }),
  actions: {
    async reviewDraft(approvalId) {
      const { data } = await api.get(`/skills/drafts/${approvalId}`)
      this.draftReviews[approvalId] = data
      return data
    },
    async fetchSettings() {
      this.settingsLoaded = false
      const { data } = await api.get('/skills/settings')
      this.personalSkillsEnabled = data.personalSkillsEnabled
      this.settingsLoaded = true
    },
    async setPersonalSkillsEnabled(enabled) {
      this.saving = true
      try {
        const { data } = await api.put('/skills/settings', { personalSkillsEnabled: enabled })
        this.personalSkillsEnabled = data.personalSkillsEnabled
      } finally { this.saving = false }
    },
    selectScope(scope) {
      const next = scope === 'instance' ? 'instance' : 'personal'
      if (next !== this.scope) this.scopedItems = []
      this.scope = next
    },
    async fetchSkills(scope) {
      this.loading = true
      try {
        const { data } = await api.get('/skills', scopeConfig(scope))
        if (scope === this.scope) this.scopedItems = data.items
        return data
      } finally {
        this.loading = false
      }
    },
    async createSkill(skill) {
      return this._save(() => api.post('/skills', skill, scopeConfig(this.scope)))
    },
    async updateSkill(id, skill) {
      return this._save(() => api.put(`/skills/${id}`, skill, scopeConfig(this.scope)))
    },
    async importSkill(file) {
      const body = new FormData()
      body.append('file', file)
      return this._save(() => api.post('/skills/import', body, scopeConfig(this.scope)))
    },
    async exportSkill(skill) {
      const { data } = await api.get(`/skills/${skill.id}/export`, { ...scopeConfig(skill.scope), responseType: 'blob' })
      return data
    },
    async readFile(skill, path) {
      return api.get(`/skills/${skill.id}/files`, { params: { scope: skill.scope, path }, responseType: 'arraybuffer' })
    },
    async writeFile(skill, path, file, executable = false) {
      const body = new FormData()
      body.append('file', file)
      return this._save(() => api.put(`/skills/${skill.id}/files`, body, {
        params: { scope: skill.scope, path, executable, expected_revision: skill.revision }
      }))
    },
    async deleteFile(skill, path) {
      return this._save(() => api.delete(`/skills/${skill.id}/files`, {
        params: { scope: skill.scope, path, expected_revision: skill.revision }
      }))
    },
    async setEnabled(id, enabled) {
      return this._save(() => api.patch(`/skills/${id}/enabled`, { enabled }, scopeConfig(this.scope)))
    },
    async resetSkill(id) {
      return this._save(() => api.post(`/skills/${id}/reset`, null, scopeConfig(this.scope)))
    },
    async deleteSkill(id) {
      this.saving = true
      try {
        await api.delete(`/skills/${id}`, scopeConfig(this.scope))
        this.scopedItems = this.scopedItems.filter((item) => item.id !== id)
      } finally {
        this.saving = false
      }
    },
    async _save(request) {
      this.saving = true
      try {
        const { data } = await request()
        const index = this.scopedItems.findIndex((item) => item.id === data.id)
        if (index >= 0) this.scopedItems[index] = data
        else this.scopedItems.push(data)
        return data
      } finally {
        this.saving = false
      }
    }
  }
})
