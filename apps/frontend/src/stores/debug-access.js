import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useDebugAccessStore = defineStore('debug-access', {
  state: () => ({
    configured: false,
    createdAt: null,
    loading: false,
    saving: false
  }),
  actions: {
    async fetchStatus() {
      this.loading = true
      try {
        const { data } = await api.get('/auth/debug-token')
        this.configured = data.configured
        this.createdAt = data.createdAt
      } finally {
        this.loading = false
      }
    },
    async createToken() {
      this.saving = true
      try {
        const { data } = await api.post('/auth/debug-token')
        this.configured = true
        this.createdAt = data.createdAt
        return data
      } finally {
        this.saving = false
      }
    },
    async revokeToken() {
      this.saving = true
      try {
        await api.delete('/auth/debug-token')
        this.configured = false
        this.createdAt = null
      } finally {
        this.saving = false
      }
    }
  }
})
