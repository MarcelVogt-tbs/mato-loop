import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useUsersStore = defineStore('users', {
  state: () => ({ items: [], loading: false, loaded: false, loadFailed: false }),
  actions: {
    async fetchAll() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/users')
        this.items = data.items
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async create(payload) {
      const { data } = await api.post('/users', payload)
      this.items = [...this.items, data].sort((a, b) => a.username.localeCompare(b.username))
      return data
    },
    async update(id, payload) {
      const { data } = await api.patch(`/users/${id}`, payload)
      this.items = this.items.map((user) => user.id === id ? data : user)
      return data
    },
    async resetPassword(id, password) {
      await api.put(`/users/${id}/password`, { password })
    },
    async remove(id) {
      await api.delete(`/users/${id}`)
      this.items = this.items.filter((user) => user.id !== id)
    }
  }
})
