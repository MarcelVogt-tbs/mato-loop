import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const emptySearch = () => ({
  configured: false,
  enabled: false,
  effectiveEnabled: false,
  defaultIntegrationId: null,
  unavailableReason: 'integration.notConfigured',
  providers: []
})

export const useIntegrationStore = defineStore('integrations', {
  state: () => ({ search: emptySearch(), loading: false }),
  actions: {
    async fetchSearch() {
      this.loading = true
      try {
        this.search = (await api.get('/integrations/search')).data
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
    async setDefaultSearch(id) {
      await api.put('/integrations/search/default', { integration_id: id })
      await this.fetchSearch()
    },
    async testSearch(id) {
      return (await api.post(`/integrations/search/providers/${id}/test`)).data
    }
  }
})
