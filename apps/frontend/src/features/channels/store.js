import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

export const useChannelStore = defineStore('channels', {
  state: () => ({
    enabled: false,
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
      this.enabled = data.enabled === true
      return data
    },
    async fetchEndpoints(config) {
      const { data } = config
        ? await api.get('/channels/endpoints', config)
        : await api.get('/channels/endpoints')
      this.endpoints = data.endpoints
      return data
    },
    async fetchAll(config, includeEndpoints = true) {
      this.loading = true
      this.loadFailed = false
      try {
        await Promise.all([this.fetchProviders(config), ...(includeEndpoints ? [this.fetchEndpoints(config)] : [])])
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
    async setEnabled(enabled) {
      const { data } = await api.put('/channels/settings', { enabled })
      this.enabled = data.enabled
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
    },
    async fetchTeamsConfiguration() {
      const { data } = await api.get('/channels/teams/configuration')
      return data
    },
    async saveTeamsConfiguration(payload) {
      const { data } = await api.put('/channels/teams/configuration', payload)
      return data
    },
    async testTeamsConfiguration() {
      const { data } = await api.post('/channels/teams/configuration/test')
      return data
    },
    async exportTeamsAppPackage(payload) {
      const { data } = await api.post('/channels/teams/configuration/app-package', payload, { responseType: 'blob' })
      return data
    },
    async saveEndpoint(provider, id, payload) {
      if (provider === 'telegram') return id ? this.updateTelegram(id, payload) : this.createTelegram(payload)
      const request = { name: payload.name, enabled: payload.enabled }
      const { data } = id
        ? await api.put(`/channels/teams/endpoints/${id}`, request)
        : await api.post('/channels/teams/endpoints', request)
      await this.fetchEndpoints()
      return data
    },
    async removeEndpoint(endpoint) {
      if (endpoint.provider === 'telegram') return this.removeTelegram(endpoint.id)
      await api.delete(`/channels/teams/endpoints/${endpoint.id}`)
      await this.fetchEndpoints()
    },
    async startPairing(provider, id) {
      if (provider === 'telegram') return this.startTelegramPairing(id)
      const { data } = await api.post(`/channels/teams/endpoints/${id}/pairing`)
      await this.fetchEndpoints()
      return data
    },
    async confirmTeamsPairing(id, candidateId) {
      const { data } = await api.post(`/channels/teams/endpoints/${id}/confirm`, { candidateId })
      await this.fetchEndpoints()
      return data
    }
  }
})
