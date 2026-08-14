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

const emptyKnowledge = () => ({
  configured: false,
  bases: []
})

const emptyGit = () => ({
  configured: false,
  instances: []
})

export const useIntegrationStore = defineStore('integrations', {
  state: () => ({
    search: emptySearch(),
    calendar: emptyCalendar(),
    knowledge: emptyKnowledge(),
    git: emptyGit(),
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
    async fetchKnowledge() {
      this.loading = true
      this.loadFailed = false
      try {
        this.knowledge = (await api.get('/integrations/knowledge')).data
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async createKnowledge(payload) {
      await api.post('/integrations/knowledge/bases', payload)
      await this.fetchKnowledge()
    },
    async updateKnowledge(id, payload) {
      const request = { ...payload }
      if (!request.token_id || !request.token_secret) {
        delete request.token_id
        delete request.token_secret
      }
      await api.put(`/integrations/knowledge/bases/${id}`, request)
      await this.fetchKnowledge()
    },
    async removeKnowledge(id) {
      await api.delete(`/integrations/knowledge/bases/${id}`)
      delete this.accessByIntegration[id]
      await this.fetchKnowledge()
    },
    async testKnowledge(id) {
      return (await api.post(`/integrations/knowledge/bases/${id}/test`)).data
    },
    async fetchGit() {
      this.loading = true
      this.loadFailed = false
      try {
        this.git = (await api.get('/integrations/git')).data
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async createGit(payload) {
      await api.post('/integrations/git', payload)
      await this.fetchGit()
    },
    async updateGit(id, payload) {
      await api.put(`/integrations/git/${id}`, payload)
      await this.fetchGit()
    },
    async removeGit(id) {
      await api.delete(`/integrations/git/${id}`)
      delete this.accessByIntegration[id]
      await this.fetchGit()
    },
    async fetchAccess(id) {
      const { data } = await api.get(`/integrations/${id}/access`)
      this.accessByIntegration[id] = data
      return data
    },
    async saveAccess(id, payload) {
      const { data } = await api.put(`/integrations/${id}/access`, payload)
      this.accessByIntegration[id] = data
      return data
    }
  }
})
