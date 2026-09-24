import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useChannelStore = defineStore('channels', {
  state: () => ({
    providers: [],
    endpoints: [],
    loading: false,
    loaded: false,
    loadFailed: false
  }),
  actions: {
    async fetchProviders(config) {
      const { data } = config
        ? await api.get('/channels/providers', config)
        : await api.get('/channels/providers')
      this.providers = data.providers
      return data
    },
    async fetchEndpoints(config) {
      const { data } = config
        ? await api.get('/channels/endpoints', config)
        : await api.get('/channels/endpoints')
      this.endpoints = data.endpoints
      return data
    },
    async fetchAll(config) {
      this.loading = true
      this.loadFailed = false
      try {
        await Promise.all([this.fetchProviders(config), this.fetchEndpoints(config)])
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async setProviderAllowed(provider, allowed) {
      await api.put(`/channels/providers/${provider}`, { allowed })
      await this.fetchProviders()
    },
    async createTelegram(payload) {
      const { data } = await api.post('/channels/telegram/endpoints', payload)
      await this.fetchEndpoints()
      return data
    },
    async updateTelegram(id, payload) {
      const request = { ...payload }
      if (!request.token) delete request.token
      const { data } = await api.put(`/channels/telegram/endpoints/${id}`, request)
      await this.fetchEndpoints()
      return data
    },
    async removeTelegram(id) {
      await api.delete(`/channels/telegram/endpoints/${id}`)
      await this.fetchEndpoints()
    },
    async testTelegram(id) {
      const { data } = await api.post(`/channels/telegram/endpoints/${id}/test`)
      await this.fetchEndpoints()
      return data
    },
    async startTelegramPairing(id) {
      const { data } = await api.post(`/channels/telegram/endpoints/${id}/pairing`)
      await this.fetchEndpoints()
      return data
    }
  }
})
