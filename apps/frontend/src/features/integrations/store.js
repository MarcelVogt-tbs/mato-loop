import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { notifyError } from 'src/utils/notifications'
import { useAuthStore } from 'stores/auth'
import { browserStatusLabel } from 'src/features/browser/helpers'

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

const emptyMail = () => ({
  configured: false,
  accounts: []
})

const emptyTbsDesk = () => ({
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

const emptyDocuments = () => ({ configured: false, accounts: [] })

const emptyArcane = () => ({ configured: false, instances: [] })

const emptyMusic = () => ({
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

function withoutEmptyPassword(payload) {
  const request = { ...payload }
  if (request.password === '') delete request.password
  return request
}

function integrationActions(name, path, {
  fetchName = `fetch${name}`, scoped = true, prepareUpdate = (payload) => payload
} = {}) {
  return {
    async [`create${name}`](payload) {
      const context = scopeContext(this)
      await api.post(path, payload, ...(scoped ? [scopeConfig(context.scope)] : []))
      if (contextIsCurrent(this, context)) await this[fetchName]()
    },
    async [`update${name}`](id, payload) {
      const context = scopeContext(this)
      await api.put(`${path}/${id}`, prepareUpdate(payload), ...(scoped ? [scopeConfig(context.scope)] : []))
      if (contextIsCurrent(this, context)) await this[fetchName]()
    },
    async [`remove${name}`](id) {
      const context = scopeContext(this)
      await api.delete(`${path}/${id}`, ...(scoped ? [scopeConfig(context.scope)] : []))
      if (contextIsCurrent(this, context)) await this[fetchName]()
    },
    async [`test${name}`](id) {
      return (await api.post(`${path}/${id}/test`, ...(scoped ? [null, scopeConfig(this.scope)] : []))).data
    }
  }
}

function clearIntegrations(store) {
  store.webfetch = { providers: [] }
  store.search = emptySearch()
  store.calendar = emptyCalendar()
  store.mail = emptyMail()
  store.microsoft365 = emptyMail()
  store.tbsDesk = emptyTbsDesk()
  store.knowledge = emptyKnowledge()
  store.recipes = emptyRecipes()
  store.documents = emptyDocuments()
  store.arcane = emptyArcane()
  store.music = emptyMusic()
  store.openscad = emptyOpenScad()
  store.coding = emptyCoding()
  store.browser = { configured: false, instances: [] }
  store.browserLoaded = false
  store.browserLoadVersion += 1
  store.browserStatuses = {}
}

export const useIntegrationStore = defineStore('integrations', {
  state: () => ({
    webfetch: { providers: [] },
    search: emptySearch(),
    calendar: emptyCalendar(),
    mail: emptyMail(),
    microsoft365: emptyMail(),
    tbsDesk: emptyTbsDesk(),
    knowledge: emptyKnowledge(),
    recipes: emptyRecipes(),
    documents: emptyDocuments(),
    arcane: emptyArcane(),
    music: emptyMusic(),
    openscad: emptyOpenScad(),
    coding: emptyCoding(),
    browser: { configured: false, instances: [] },
    browserLoaded: false,
    browserStatuses: {},
    browserLoadVersion: 0,
    scope: 'instance',
    catalog: [],
    catalogLoading: false,
    catalogLoaded: false,
    catalogLoadFailed: false,
    pendingLoads: 0,
    loading: false,
    loaded: false,
    loadFailed: false,
    scopeGeneration: 0,
    loadVersions: {},
    effectiveLoadVersion: 0
  }),
  getters: {
    configuredIntegrations: (state) => [
      ...state.webfetch.providers,
      ...state.search.providers, ...state.calendar.accounts, ...state.mail.accounts, ...state.microsoft365.accounts,
      ...state.knowledge.bases, ...state.recipes.accounts, ...state.documents.accounts, ...state.arcane.instances,
      ...state.music.instances, ...state.openscad.instances, ...state.coding.instances,
      ...state.tbsDesk.accounts,
      ...state.browser.instances.map((item) => ({ ...item, integrationType: 'browser', providerType: 'browser' }))
    ],
    adapterAvailable: (state) => (adapterType, scope = state.scope) => {
      const adapter = state.catalog.find((item) => item.adapterType === adapterType)
      if (!adapter || state.catalogLoadFailed) return false
      if (scope === 'personal' && adapter.instanceOnly) return false
      if (scope === 'personal') return adapter.availability === 'full' || useAuthStore().isAdmin && adapter.availability === 'instance_only'
      return !adapter.personalOnly && adapter.executionLocation === 'backend'
        && ['full', 'instance_only'].includes(adapter.availability)
    }
  },
  actions: {
    ...integrationActions('Search', '/integrations/search/providers'),
    ...integrationActions('Webfetch', '/integrations/webfetch/providers', { scoped: false }),
    ...integrationActions('Calendar', '/integrations/calendar/accounts', { prepareUpdate: withoutEmptyPassword }),
    ...integrationActions('Mail', '/integrations/mail/accounts', { prepareUpdate: withoutEmptyPassword }),
    ...integrationActions('Microsoft365', '/integrations/microsoft365'),
    async startMicrosoft365Login(id) {
      return (await api.post(`/integrations/microsoft365/${id}/login`, null, scopeConfig(this.scope))).data
    },
    async finishMicrosoft365Login(attemptId) {
      return (await api.post(`/integrations/microsoft365/oauth/attempts/${attemptId}/finish`)).data
    },
    async cancelMicrosoft365Login(attemptId) {
      await api.delete(`/integrations/microsoft365/oauth/attempts/${attemptId}`)
    },
    async disconnectMicrosoft365(id) {
      const context = scopeContext(this)
      await api.post(`/integrations/microsoft365/${id}/disconnect`, null, scopeConfig(context.scope))
      if (!contextIsCurrent(this, context)) return
      this.loadVersions.microsoft365 = (this.loadVersions.microsoft365 || 0) + 1
      const account = this.microsoft365.accounts.find((item) => item.id === id)
      if (account) Object.assign(account, { hasCredential: false, authStatus: 'authorization_required' })
    },
    ...integrationActions('TbsDesk', '/integrations/support/tbs-desk', { scoped: false, prepareUpdate: withoutEmptyPassword }),
    ...integrationActions('Knowledge', '/integrations/knowledge/bases', {
      prepareUpdate(payload) {
        const request = { ...payload }
        if (!request.token_id || !request.token_secret) {
          delete request.token_id
          delete request.token_secret
        }
        return request
      }
    }),
    ...integrationActions('Recipes', '/integrations/recipes/accounts', {
      prepareUpdate(payload) {
        const request = { ...payload }
        if (typeof request.token === 'string' && request.token.trim() === '') delete request.token
        return request
      }
    }),
    ...integrationActions('Documents', '/integrations/documents/accounts', {
      prepareUpdate(payload) {
        const request = { ...payload }
        if (typeof request.token === 'string' && request.token.trim() === '') delete request.token
        return request
      }
    }),
    ...integrationActions('Arcane', '/integrations/infrastructure/arcane', {
      prepareUpdate(payload) {
        const request = { ...payload }
        if (!request.api_key) delete request.api_key
        return request
      }
    }),
    ...integrationActions('SonoBin', '/integrations/music/sonobin', {
      fetchName: 'fetchMusic',
      prepareUpdate(payload) {
        const request = { ...payload }
        if (!request.bearerToken) delete request.bearerToken
        return request
      }
    }),
    ...integrationActions('OpenScad', '/integrations/openscad'),
    async testIntegration(integration) {
      if (integration.integrationType === 'browser') {
        const result = await this.browserStatus(integration.id, true)
        return { type: result.state === 'ready' ? 'positive' : 'warning', message: browserStatusLabel(result) }
      }
      const method = { webfetch: 'testWebfetch', search: 'testSearch', calendar: 'testCalendar', mail: 'testMail', microsoft365: 'testMicrosoft365', support: 'testTbsDesk', knowledge: 'testKnowledge', recipes: 'testRecipes', documents: 'testDocuments', infrastructure: 'testArcane', music: 'testSonoBin', openscad: 'testOpenScad', coding: 'testOpenCode' }[integration.integrationType]
      const result = await this[method](integration.id)
      return integration.integrationType === 'openscad' && result?.headlessPreview === false
        ? { type: 'warning', message: 'OpenSCAD ist erreichbar, unterstützt aber keine Headless-Vorschau.' }
        : { type: 'positive', message: 'Verbindung erfolgreich' }
    },
    async fetchProfileAvailability(id, scope) {
      return (await api.get(`/integrations/profiles/${id}/availability`, { params: { scope } })).data
    },
    async saveProfileAvailability(id, scope, value) {
      return (await api.put(`/integrations/profiles/${id}/availability`, value, { params: { scope } })).data
    },
    async fetchPermissions(id, profileId = null) {
      return (await api.get(`/integrations/${id}/permissions`, { params: { scope: this.scope, profile_id: profileId } })).data
    },
    async savePermissions(id, value, profileId = null) {
      return (await api.put(`/integrations/${id}/permissions`, value, { params: { scope: this.scope, profile_id: profileId } })).data
    },
    async setIntegrationEnabled(id, enabled, scope) {
      const { data } = await api.patch(`/integrations/${id}/enabled`, { enabled }, scopeConfig(scope))
      return data
    },
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
          this.fetchMicrosoft365(),
          this.fetchKnowledge(),
          this.fetchRecipes(),
          this.fetchDocuments(),
          this.fetchArcane(),
          this.fetchMusic(),
          this.fetchOpenScad(),
          ...(context.scope === 'instance' ? [this.fetchWebfetch()] : []),
          ...(context.scope === 'personal' ? [this.fetchCoding(), this.fetchBrowser(), this.fetchTbsDesk()] : [])
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
    fetchWebfetch() {
      return this.fetchResource('webfetch', '/integrations/webfetch')
    },
    fetchCalendar() {
      return this.fetchResource('calendar', '/integrations/calendar')
    },
    fetchMail() {
      return this.fetchResource('mail', '/integrations/mail')
    },
    fetchMicrosoft365() {
      return this.fetchResource('microsoft365', '/integrations/microsoft365')
    },
    async fetchTbsDesk() {
      const context = scopeContext(this)
      const loadVersion = (this.loadVersions.tbsDesk || 0) + 1
      this.loadVersions.tbsDesk = loadVersion
      this.pendingLoads += 1
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/integrations/support/tbs-desk')
        if (!contextIsCurrent(this, context) || this.loadVersions.tbsDesk !== loadVersion) return
        this.tbsDesk = data
        this.loaded = true
        return data
      } catch (error) {
        if (contextIsCurrent(this, context) && this.loadVersions.tbsDesk === loadVersion) this.loadFailed = true
        throw error
      } finally {
        if (contextIsCurrent(this, context)) {
          this.pendingLoads = Math.max(0, this.pendingLoads - 1)
          this.loading = this.pendingLoads > 0
        }
      }
    },
    fetchKnowledge() {
      return this.fetchResource('knowledge', '/integrations/knowledge')
    },
    fetchRecipes() {
      return this.fetchResource('recipes', '/integrations/recipes')
    },
    fetchDocuments() {
      return this.fetchResource('documents', '/integrations/documents')
    },
    fetchArcane() {
      return this.fetchResource('arcane', '/integrations/infrastructure/arcane')
    },
    fetchMusic() {
      return this.fetchResource('music', '/integrations/music/sonobin')
    },
    fetchOpenScad() {
      return this.fetchResource('openscad', '/integrations/openscad')
    },
    async discoverOpenScad(executionAgentId) {
      const scope = this.scope
      return (await api.post(
        '/integrations/openscad/discover',
        { executionAgentId },
        scopeConfig(scope)
      )).data
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
    async fetchBrowser() {
      const version = ++this.browserLoadVersion
      let data = { configured: false, instances: [] }
      if (!this.catalogLoaded || this.catalogLoadFailed || this.adapterAvailable('browser', 'personal')) {
        try {
          data = (await api.get('/integrations/browser', scopeConfig('personal', { suppressErrorToast: true }))).data
        } catch (error) {
          if (!['integration.adapterDisabled', 'integration.personalAdapterDisabled'].includes(error?.response?.data?.error?.code)) {
            notifyError(error)
            throw error
          }
        }
      }
      if (version === this.browserLoadVersion) {
        this.browser = data
        this.browserLoaded = true
      }
      return data
    },
    async saveBrowser(id, payload) {
      const version = ++this.browserLoadVersion
      const { data } = id
        ? await api.patch(`/integrations/browser/${id}`, payload, scopeConfig('personal'))
        : await api.post('/integrations/browser', payload, scopeConfig('personal'))
      if (version === this.browserLoadVersion) {
        this.browser = data
        this.browserLoaded = true
        if (id) delete this.browserStatuses[id]
      }
      return data
    },
    async removeBrowser(id) {
      const version = ++this.browserLoadVersion
      const { data } = await api.delete(`/integrations/browser/${id}`, scopeConfig('personal'))
      if (version === this.browserLoadVersion) {
        this.browser = data
        this.browserLoaded = true
        delete this.browserStatuses[id]
      }
    },
    async browserStatus(id, test = false) {
      const version = this.browserLoadVersion
      const { data } = test
        ? await api.post(`/integrations/browser/${id}/test`, null, scopeConfig('personal'))
        : await api.get(`/integrations/browser/${id}/status`, scopeConfig('personal'))
      if (version === this.browserLoadVersion) this.browserStatuses[id] = data
      return data
    },
    async fetchAccess(id) {
      const context = scopeContext(this)
      const { data } = await api.get(`/integrations/${id}/access`, scopeConfig(context.scope))
      return contextIsCurrent(this, context) ? data : null
    },
    async saveAccess(id, payload) {
      const context = scopeContext(this)
      const { data } = await api.put(`/integrations/${id}/access`, payload, scopeConfig(context.scope))
      return contextIsCurrent(this, context) ? data : null
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
