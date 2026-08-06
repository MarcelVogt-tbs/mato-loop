import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const emptySearch = () => ({
  configured: false,
  enabled: true,
  effectiveEnabled: true,
  unavailableReason: null,
  providers: []
})

const emptyCalendar = () => ({
  configured: false,
  accounts: []
})

export const useIntegrationStore = defineStore('integrations', {
  state: () => ({
    search: emptySearch(),
    calendar: emptyCalendar(),
    accessByIntegration: {},
    loading: false,
    loaded: false,
    loadFailed: false
  }),
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
      delete this.accessByIntegration[id]
      await this.fetchSearch()
    },
    async testSearch(id) {
      return (await api.post(`/integrations/search/providers/${id}/test`)).data
    },
    async fetchCalendar() {
      this.loading = true
      this.loadFailed = false
      try {
        this.calendar = (await api.get('/integrations/calendar')).data
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async createCalendar(payload) {
      await api.post('/integrations/calendar/accounts', payload)
      await this.fetchCalendar()
    },
    async updateCalendar(id, payload) {
      const request = { ...payload }
      if (request.password === '') delete request.password
      await api.put(`/integrations/calendar/accounts/${id}`, request)
      await this.fetchCalendar()
    },
    async removeCalendar(id) {
      await api.delete(`/integrations/calendar/accounts/${id}`)
      delete this.accessByIntegration[id]
      await this.fetchCalendar()
    },
    async testCalendar(id) {
      return (await api.post(`/integrations/calendar/accounts/${id}/test`)).data
    },
    async fetchAccess(id) {
      const { data } = await api.get(`/integrations/${id}/access`)
      this.accessByIntegration[id] = data
      return data
    },
    async saveAccess(id, policies, resources) {
      const { data } = await api.put(`/integrations/${id}/access`, { policies, resources })
      this.accessByIntegration[id] = data
      return data
    }
  }
})
