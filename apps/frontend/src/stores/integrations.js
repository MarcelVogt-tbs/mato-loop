import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const emptySearch = () => ({
  configured: false,
  enabled: true,
  effectiveEnabled: true,
  unavailableReason: null,
  providers: []
})

export const useIntegrationStore = defineStore('integrations', {
  state: () => ({ search: emptySearch(), loading: false, loaded: false, loadFailed: false }),
  actions: {
    async fetchSearch() {
      this.loading = true
      this.loadFailed = false
      try {
        this.search = (await api.get('/integrations/search')).data
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async createSearch(payload) {
      await api.post('/integrations/search/providers', payload)
      await this.fetchSearch()
    },
    async updateSearch(id, payload) {
      await api.put(`/integrations/search/providers/${id}`, payload)
      await this.fetchSearch()
    },
    async removeSearch(id) {
      await api.delete(`/integrations/search/providers/${id}`)
      await this.fetchSearch()
    },
    async testSearch(id) {
      return (await api.post(`/integrations/search/providers/${id}/test`)).data
    }
  }
})
