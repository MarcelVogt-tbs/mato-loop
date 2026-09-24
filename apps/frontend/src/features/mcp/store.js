import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

function serversFrom(data) {
  return Array.isArray(data) ? data : (data?.servers || [])
}

function branchConfig(scope) {
  return { params: { scope } }
}

export const useMcpServerStore = defineStore('mcp-servers', {
  state: () => ({
    servers: [],
    visibleServers: [],
    loading: false,
    loaded: false,
    loadFailed: false,
    personalAllowed: false,
    settingsLoaded: false,
    settingsLoadFailed: false,
    settingsLoading: false,
    settingsSaving: false,
    scope: 'instance',
    loadVersion: 0
  }),
  actions: {
    selectScope(scope) {
      const next = scope === 'personal' ? 'personal' : 'instance'
      if (next === this.scope) return
      this.scope = next
      this.servers = []
      this.loaded = false
      this.loadVersion += 1
      this.loading = false
    },
    async fetchServers() {
      const version = ++this.loadVersion
      this.loading = true
      this.loadFailed = false
      try {
        const scope = this.scope
        const { data } = await api.get('/mcp/servers', branchConfig(scope))
        if (this.loadVersion !== version || this.scope !== scope) return
        this.servers = serversFrom(data)
        this.loaded = true
        return this.servers
      } catch (error) {
        if (this.loadVersion === version) this.loadFailed = true
        throw error
      } finally {
        if (this.loadVersion === version) this.loading = false
      }
    },
    async fetchVisibleServers() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/mcp/servers/visible')
        this.visibleServers = serversFrom(data)
        this.loaded = true
        return this.visibleServers
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally {
        this.loading = false
      }
    },
    async fetchSettings() {
      this.settingsLoading = true
      this.settingsLoadFailed = false
      try {
        const { data } = await api.get('/mcp/settings')
        this.personalAllowed = data.personalAllowed === true
        this.settingsLoaded = true
        return data
      } catch (error) {
        this.settingsLoadFailed = true
        throw error
      } finally {
        this.settingsLoading = false
      }
    },
    async saveSettings(personalAllowed) {
      this.settingsSaving = true
      try {
        const { data } = await api.put('/mcp/settings', { personalAllowed })
        this.personalAllowed = data.personalAllowed === true
        this.settingsLoaded = true
        return data
      } finally {
        this.settingsSaving = false
      }
    },
    async createServer(payload) {
      const scope = this.scope
      const { data } = await api.post('/mcp/servers', payload, branchConfig(scope))
      if (this.scope === scope) this.servers.push(data)
      return data
    },
    async updateServer(id, payload) {
      const scope = this.scope
      const request = { ...payload }
      if (!request.bearerToken) delete request.bearerToken
      const { data } = await api.put(`/mcp/servers/${id}`, request, branchConfig(scope))
      if (this.scope === scope) {
        const index = this.servers.findIndex((server) => server.id === id)
        if (index === -1) this.servers.push(data)
        else this.servers[index] = data
      }
      return data
    },
    async removeServer(id) {
      const scope = this.scope
      await api.delete(`/mcp/servers/${id}`, branchConfig(scope))
      if (this.scope === scope) this.servers = this.servers.filter((server) => server.id !== id)
    },
    async refreshServer(id) {
      const scope = this.scope
      const data = (await api.post(`/mcp/servers/${id}/refresh`, null, branchConfig(scope))).data
      if (this.scope === scope) {
        const index = this.servers.findIndex((server) => server.id === id)
        if (index === -1) this.servers.push(data)
        else this.servers[index] = data
      }
      return data
    }
  }
})
