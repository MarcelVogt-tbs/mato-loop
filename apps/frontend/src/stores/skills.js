import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useSkillStore = defineStore('skills', {
  state: () => ({ items: [], loading: false }),
  actions: {
    async fetchSkills() {
      this.loading = true
      try {
        const { data } = await api.get('/skills')
        this.items = data.items
        return data
      } finally {
        this.loading = false
      }
    },
    async createSkill(skill) {
      const { data } = await api.post('/skills', skill)
      this.items.push(data)
      return data
    },
    async updateSkill(id, skill) {
      const { data } = await api.put(`/skills/${id}`, skill)
      const index = this.items.findIndex((item) => item.id === id)
      if (index >= 0) this.items[index] = data
      return data
    },
    async deleteSkill(id) {
      await api.delete(`/skills/${id}`)
      this.items = this.items.filter((item) => item.id !== id)
    }
  }
})
