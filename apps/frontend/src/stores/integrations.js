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

const emptyOpenScad = () => ({
  configured: false,
  instances: []
})

const PERSONAL_ADAPTER_TYPES = ['searxng', 'caldav', 'bookstack', 'git', 'openscad']

function scopeConfig(scope, config = {}) {
  return { ...config, params: { ...(config.params || {}), scope } }
}

function assignGate(store, data) {
  if ('personalIntegrationsEnabled' in (data || {})) {
    store.personalIntegrationsEnabled = data.personalIntegrationsEnabled !== false
  }
  const allowed = data?.allowedPersonalAdapterTypes || data?.allowedAdapterTypes
  if (Array.isArray(allowed)) store.allowedPersonalAdapterTypes = allowed
}

function scopeContext(store) {
  return { scope: store.scope, generation: store.scopeGeneration }
}

function contextIsCurrent(store, context) {
  return store.scope === context.scope && store.scopeGeneration === context.generation
}

function clearIntegrations(store) {
  store.search = emptySearch()
  store.calendar = emptyCalendar()
  store.knowledge = emptyKnowledge()
  store.git = emptyGit()
  store.openscad = emptyOpenScad()
}

export const useIntegrationStore = defineStore('integrations', {
  state: () => ({
    search: emptySearch(),
    calendar: emptyCalendar(),
    knowledge: emptyKnowledge(),
    git: emptyGit(),
    openscad: emptyOpenScad(),
    scope: 'instance',
    personalIntegrationsEnabled: true,
    allowedPersonalAdapterTypes: [...PERSONAL_ADAPTER_TYPES],
    accessByIntegration: {},
    pendingLoads: 0,
    loading: false,
    loaded: false,
    loadFailed: false,
    scopeGeneration: 0,
    loadVersions: {},
    effectiveLoadVersion: 0
  }),
  getters: {
    adapterAllowed: (state) => (adapterType) => state.scope === 'instance'
      || state.allowedPersonalAdapterTypes.includes(adapterType)
  },
  actions: {
    selectScope(scope) {
      const next = scope === 'personal' ? 'personal' : 'instance'
      if (next === this.scope) return
      this.scope = next
      this.scopeGeneration += 1
      this.loadVersions = {}
      this.pendingLoads = 0
      this.loading = false
      this.loaded = false
      this.loadFailed = false
      this.accessByIntegration = {}
      clearIntegrations(this)
    },
    async fetchPersonalPolicy() {
      const context = scopeContext(this)
      const { data } = await api.get('/integrations/personal-settings')
      if (!contextIsCurrent(this, context)) return false
      this.personalIntegrationsEnabled = data.enabled !== false
      if (Array.isArray(data.allowedAdapterTypes)) {
        this.allowedPersonalAdapterTypes = data.allowedAdapterTypes
      }
      return this.personalIntegrationsEnabled
    },
    async fetchAll() {
      const context = scopeContext(this)
      this.loadFailed = false
      try {
        if (context.scope === 'personal') {
          await this.fetchPersonalPolicy()
          if (!contextIsCurrent(this, context)) return
          if (!this.personalIntegrationsEnabled) {
            clearIntegrations(this)
            this.loaded = true
            this.loadFailed = false
            return
          }
        }
        await Promise.all([
          this.fetchSearch(),
          this.fetchCalendar(),
          this.fetchKnowledge(),
          this.fetchGit(),
          this.fetchOpenScad()
        ])
      } catch (error) {
        if (contextIsCurrent(this, context)) this.loadFailed = true
        throw error
      }
    },
    async fetchResource(field, path) {
      const context = scopeContext(this)
      const loadVersion = (this.loadVersions[field] || 0) + 1
      this.loadVersions[field] = loadVersion
      this.pendingLoads += 1
      this.loading = true
      this.loadFailed = false
      try {
        const data = (await api.get(path, scopeConfig(context.scope))).data
        if (!contextIsCurrent(this, context) || this.loadVersions[field] !== loadVersion) return
        this[field] = data
        assignGate(this, data)
        this.loaded = true
        return data
      } catch (error) {
        if (contextIsCurrent(this, context) && this.loadVersions[field] === loadVersion) {
          this.loadFailed = true
        }
        throw error
      } finally {
        if (contextIsCurrent(this, context)) {
          this.pendingLoads = Math.max(0, this.pendingLoads - 1)
          this.loading = this.pendingLoads > 0
        }
      }
    },
    fetchSearch() {
      return this.fetchResource('search', '/integrations/search')
    },
    async createSearch(payload) {
      const context = scopeContext(this)
      await api.post('/integrations/search/providers', payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchSearch()
    },
    async updateSearch(id, payload) {
      const context = scopeContext(this)
      await api.put(`/integrations/search/providers/${id}`, payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchSearch()
    },
    async removeSearch(id) {
      const context = scopeContext(this)
      await api.delete(`/integrations/search/providers/${id}`, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return
      delete this.accessByIntegration[id]
      await this.fetchSearch()
    },
    async testSearch(id) {
      const scope = this.scope
      return (await api.post(`/integrations/search/providers/${id}/test`, null, scopeConfig(scope))).data
    },
    fetchCalendar() {
      return this.fetchResource('calendar', '/integrations/calendar')
    },
    async createCalendar(payload) {
      const context = scopeContext(this)
      await api.post('/integrations/calendar/accounts', payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchCalendar()
    },
    async updateCalendar(id, payload) {
      const request = { ...payload }
      if (request.password === '') delete request.password
      const context = scopeContext(this)
      await api.put(`/integrations/calendar/accounts/${id}`, request, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchCalendar()
    },
    async removeCalendar(id) {
      const context = scopeContext(this)
      await api.delete(`/integrations/calendar/accounts/${id}`, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return
      delete this.accessByIntegration[id]
      await this.fetchCalendar()
    },
    async testCalendar(id) {
      const scope = this.scope
      return (await api.post(`/integrations/calendar/accounts/${id}/test`, null, scopeConfig(scope))).data
    },
    fetchKnowledge() {
      return this.fetchResource('knowledge', '/integrations/knowledge')
    },
    async createKnowledge(payload) {
      const context = scopeContext(this)
      await api.post('/integrations/knowledge/bases', payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchKnowledge()
    },
    async updateKnowledge(id, payload) {
      const request = { ...payload }
      if (!request.token_id || !request.token_secret) {
        delete request.token_id
        delete request.token_secret
      }
      const context = scopeContext(this)
      await api.put(`/integrations/knowledge/bases/${id}`, request, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchKnowledge()
    },
    async removeKnowledge(id) {
      const context = scopeContext(this)
      await api.delete(`/integrations/knowledge/bases/${id}`, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return
      delete this.accessByIntegration[id]
      await this.fetchKnowledge()
    },
    async testKnowledge(id) {
      const scope = this.scope
      return (await api.post(`/integrations/knowledge/bases/${id}/test`, null, scopeConfig(scope))).data
    },
    fetchGit() {
      return this.fetchResource('git', '/integrations/git')
    },
    async createGit(payload) {
      const context = scopeContext(this)
      await api.post('/integrations/git', payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchGit()
    },
    async updateGit(id, payload) {
      const context = scopeContext(this)
      await api.put(`/integrations/git/${id}`, payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchGit()
    },
    async removeGit(id) {
      const context = scopeContext(this)
      await api.delete(`/integrations/git/${id}`, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return
      delete this.accessByIntegration[id]
      await this.fetchGit()
    },
    fetchOpenScad() {
      return this.fetchResource('openscad', '/integrations/openscad')
    },
    async createOpenScad(payload) {
      const context = scopeContext(this)
      await api.post('/integrations/openscad', payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchOpenScad()
    },
    async updateOpenScad(id, payload) {
      const context = scopeContext(this)
      await api.put(`/integrations/openscad/${id}`, payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchOpenScad()
    },
    async removeOpenScad(id) {
      const context = scopeContext(this)
      await api.delete(`/integrations/openscad/${id}`, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchOpenScad()
    },
    async discoverOpenScad(executionAgentId) {
      const scope = this.scope
      return (await api.post(
        '/integrations/openscad/discover',
        { executionAgentId },
        scopeConfig(scope)
      )).data
    },
    async testOpenScad(id) {
      const scope = this.scope
      return (await api.post(`/integrations/openscad/${id}/test`, null, scopeConfig(scope))).data
    },
    async fetchAccess(id) {
      const context = scopeContext(this)
      const { data } = await api.get(`/integrations/${id}/access`, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return null
      this.accessByIntegration[id] = data
      return data
    },
    async saveAccess(id, payload) {
      const context = scopeContext(this)
      const { data } = await api.put(`/integrations/${id}/access`, payload, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return null
      this.accessByIntegration[id] = data
      return data
    },
    async savePersonalSettings(enabled, allowedAdapterTypes) {
      const context = scopeContext(this)
      const { data } = await api.put('/integrations/personal-settings', {
        enabled,
        allowedAdapterTypes
      }, scopeConfig('instance'))
      if (!contextIsCurrent(this, context)) return
      this.personalIntegrationsEnabled = data.enabled !== false
      this.allowedPersonalAdapterTypes = data.allowedAdapterTypes || allowedAdapterTypes
    },
    async fetchEffectiveSearch() {
      const loadVersion = ++this.effectiveLoadVersion
      this.loadFailed = false
      try {
        await this.fetchPersonalPolicy()
        if (this.effectiveLoadVersion !== loadVersion) return
        if (!this.personalIntegrationsEnabled) {
          this.search = emptySearch()
          this.loaded = true
          return
        }
        const personal = (await api.get('/integrations/search', scopeConfig('personal'))).data
        if (this.effectiveLoadVersion !== loadVersion) return
        this.search = personal
        this.loaded = true
      } catch (error) {
        if (this.effectiveLoadVersion === loadVersion) this.loadFailed = true
        throw error
      }
    }
  }
})
