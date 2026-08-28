import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const emptySearch = () => ({
  configured: false,
  enabled: true,
  effectiveEnabled: true,
  unavailableReason: null,
  providers: [],
  primaryProvider: 'no_key',
  fallbackProvider: null,
  providerOptions: [
    {
      value: 'no_key',
      name: 'Websuche ohne API-Key',
      adapterType: 'ddgs',
      integrationId: null
    }
  ]
})

const emptyCalendar = () => ({
  configured: false,
  accounts: []
})

const emptyMail = () => ({
  configured: false,
  accounts: []
})

const emptyKnowledge = () => ({
  configured: false,
  bases: []
})

const emptyRecipes = () => ({
  configured: false,
  accounts: []
})

const emptyGit = () => ({
  configured: false,
  instances: []
})

const emptyOpenScad = () => ({
  configured: false,
  instances: []
})

const emptyCoding = () => ({
  configured: false,
  instances: []
})
const unavailableCodingCodes = new Set([
  'integration.adapterDisabled',
  'integration.personalAdapterDisabled',
  'integration.unavailable'
])

function scopeConfig(scope, config = {}) {
  return { ...config, params: { ...(config.params || {}), scope } }
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
  store.mail = emptyMail()
  store.knowledge = emptyKnowledge()
  store.recipes = emptyRecipes()
  store.git = emptyGit()
  store.openscad = emptyOpenScad()
  store.coding = emptyCoding()
}

export const useIntegrationStore = defineStore('integrations', {
  state: () => ({
    search: emptySearch(),
    calendar: emptyCalendar(),
    mail: emptyMail(),
    knowledge: emptyKnowledge(),
    recipes: emptyRecipes(),
    git: emptyGit(),
    openscad: emptyOpenScad(),
    coding: emptyCoding(),
    scope: 'instance',
    catalog: [],
    catalogLoading: false,
    catalogLoaded: false,
    catalogLoadFailed: false,
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
    adapterAvailable: (state) => (adapterType, scope = state.scope) => {
      const adapter = state.catalog.find((item) => item.adapterType === adapterType)
      if (!adapter || state.catalogLoadFailed) return false
      if (scope === 'personal') return adapter.availability === 'full'
      return !adapter.personalOnly && adapter.executionLocation === 'backend'
        && ['full', 'instance_only'].includes(adapter.availability)
    }
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
    async fetchCatalog() {
      this.catalogLoading = true
      this.catalogLoadFailed = false
      try {
        const { data } = await api.get('/integrations/catalog')
        this.catalog = data.adapters
        this.catalogLoaded = true
        return this.catalog
      } catch (error) {
        this.catalogLoadFailed = true
        throw error
      } finally {
        this.catalogLoading = false
      }
    },
    async setAdapterAvailability(adapterType, availability) {
      this.catalogLoading = true
      try {
        const { data } = await api.put(`/integrations/catalog/${adapterType}`, { availability })
        const index = this.catalog.findIndex((adapter) => adapter.adapterType === adapterType)
        if (index !== -1) this.catalog[index] = data
        return data
      } finally {
        this.catalogLoading = false
      }
    },
    async fetchAll() {
      const context = scopeContext(this)
      this.loadFailed = false
      try {
        await Promise.all([
          this.fetchSearch(),
          this.fetchCalendar(),
          this.fetchMail(),
          this.fetchKnowledge(),
          this.fetchRecipes(),
          this.fetchGit(),
          this.fetchOpenScad(),
          ...(context.scope === 'personal' ? [this.fetchCoding()] : [])
        ])
      } catch (error) {
        if (contextIsCurrent(this, context)) this.loadFailed = true
        throw error
      }
    },
    async fetchResource(field, path, config) {
      const context = scopeContext(this)
      const loadVersion = (this.loadVersions[field] || 0) + 1
      this.loadVersions[field] = loadVersion
      this.pendingLoads += 1
      this.loading = true
      this.loadFailed = false
      try {
        const data = (await api.get(path, scopeConfig(context.scope, config))).data
        if (!contextIsCurrent(this, context) || this.loadVersions[field] !== loadVersion) return
        this[field] = data
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
    async saveSearchSelection(primaryProvider, fallbackProvider) {
      const context = scopeContext(this)
      const { data } = await api.put('/integrations/search', {
        primaryProvider,
        fallbackProvider
      }, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return null
      this.search = data
      return data
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
    fetchMail() {
      return this.fetchResource('mail', '/integrations/mail')
    },
    async createMail(payload) {
      const context = scopeContext(this)
      await api.post('/integrations/mail/accounts', payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchMail()
    },
    async updateMail(id, payload) {
      const request = { ...payload }
      if (request.password === '') delete request.password
      const context = scopeContext(this)
      await api.put(`/integrations/mail/accounts/${id}`, request, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchMail()
    },
    async removeMail(id) {
      const context = scopeContext(this)
      await api.delete(`/integrations/mail/accounts/${id}`, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return
      delete this.accessByIntegration[id]
      await this.fetchMail()
    },
    async testMail(id) {
      const scope = this.scope
      return (await api.post(`/integrations/mail/accounts/${id}/test`, null, scopeConfig(scope))).data
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
    fetchRecipes() {
      return this.fetchResource('recipes', '/integrations/recipes')
    },
    async createRecipes(payload) {
      const context = scopeContext(this)
      await api.post('/integrations/recipes/accounts', payload, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchRecipes()
    },
    async updateRecipes(id, payload) {
      const request = { ...payload }
      if (typeof request.token === 'string' && request.token.trim() === '') delete request.token
      const context = scopeContext(this)
      await api.put(`/integrations/recipes/accounts/${id}`, request, scopeConfig(context.scope))
      if (contextIsCurrent(this, context)) await this.fetchRecipes()
    },
    async removeRecipes(id) {
      const context = scopeContext(this)
      await api.delete(`/integrations/recipes/accounts/${id}`, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return
      delete this.accessByIntegration[id]
      await this.fetchRecipes()
    },
    async testRecipes(id) {
      const scope = this.scope
      return (await api.post(`/integrations/recipes/accounts/${id}/test`, null, scopeConfig(scope))).data
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
    async fetchCoding({ personal = false } = {}) {
      if (personal && this.scope !== 'personal') {
        try {
          return (await api.get('/integrations/coding', scopeConfig('personal', {
            suppressErrorToast: true
          }))).data
        } catch (error) {
          if (!unavailableCodingCodes.has(error?.response?.data?.error?.code)) throw error
          return emptyCoding()
        }
      }
      const context = scopeContext(this)
      if (context.scope !== 'personal') return emptyCoding()
      if (this.catalogLoaded && !this.adapterAvailable('opencode', 'personal')) {
        this.coding = emptyCoding()
        return this.coding
      }
      try {
        return await this.fetchResource(
          'coding',
          '/integrations/coding',
          { suppressErrorToast: true }
        )
      } catch (error) {
        if (!unavailableCodingCodes.has(error?.response?.data?.error?.code)) throw error
        if (contextIsCurrent(this, context)) {
          this.coding = emptyCoding()
          this.loadFailed = false
          this.loaded = true
        }
        return this.coding
      }
    },
    async createOpenCode(payload) {
      const context = scopeContext(this)
      this.loadVersions.coding = (this.loadVersions.coding || 0) + 1
      const request = { ...payload }
      if (!request.password) delete request.password
      const { data } = await api.post('/integrations/coding/opencode', request)
      if (contextIsCurrent(this, context)) this.coding = data
      return data
    },
    async updateOpenCode(id, payload) {
      const context = scopeContext(this)
      this.loadVersions.coding = (this.loadVersions.coding || 0) + 1
      const request = { ...payload }
      if (!request.password) delete request.password
      const { data } = await api.put(`/integrations/coding/opencode/${id}`, request)
      if (contextIsCurrent(this, context)) this.coding = data
      return data
    },
    async removeOpenCode(id) {
      const context = scopeContext(this)
      this.loadVersions.coding = (this.loadVersions.coding || 0) + 1
      await api.delete(`/integrations/coding/opencode/${id}`)
      if (contextIsCurrent(this, context)) await this.fetchCoding()
    },
    async testOpenCode(id) {
      return (await api.post(`/integrations/coding/opencode/${id}/test`)).data
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
    async fetchEffectiveSearch() {
      const loadVersion = ++this.effectiveLoadVersion
      this.loadFailed = false
      try {
        const personal = await api.get('/integrations/search', scopeConfig('personal'))
        if (this.effectiveLoadVersion !== loadVersion) return
        this.search = personal.data
        this.loaded = true
      } catch (error) {
        if (this.effectiveLoadVersion === loadVersion) this.loadFailed = true
        throw error
      }
    }
  }
})
