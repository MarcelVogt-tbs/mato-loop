import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const usePersonalVariableStore = defineStore('personal-variables', {
  state: () => ({
    variables: [],
    loading: false,
    loaded: false,
    loadFailed: false
  }),
  actions: {
    async fetchVariables() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/personal-variables')
        this.variables = Array.isArray(data) ? data : []
        this.loaded = true
        return this.variables
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async createVariable(payload) {
      const { data } = await api.post('/personal-variables', payload)
      this.variables.push(data)
      return data
    },
    async updateVariable(id, payload) {
      const { data } = await api.put(`/personal-variables/${id}`, payload)
      const index = this.variables.findIndex((variable) => variable.id === id)
      if (index === -1) this.variables.push(data)
      else this.variables[index] = data
      return data
    },
    async removeVariable(id) {
      await api.delete(`/personal-variables/${id}`)
      this.variables = this.variables.filter((variable) => variable.id !== id)
    }
  }
})
