import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const scopeConfig = (scope) => ({ params: { scope } })
const defaultSettings = () => ({
  scope: 'personal',
  configuredDisplayName: null,
  effectiveDisplayName: 'MatoLoop',
  personalDisplayNamesEnabled: true,
  personalPersonasEnabled: true,
  defaultPersonaId: null,
  effectiveDefaultPersonaId: null,
  personas: []
})

export const useAgentIdentityStore = defineStore('agent-identity', {
  state: () => ({
    displayName: 'MatoLoop',
    loaded: false,
    loadFailed: false,
    loading: false,
    requestRevision: 0,
    ...defaultSettings(),
    settingsLoaded: false,
    settingsLoadFailed: false,
    settingsLoading: false,
    settingsSaving: false,
    settingsRevision: 0
  }),
  actions: {
    resetIdentity() {
      this.requestRevision += 1
      this.settingsRevision += 1
      this.displayName = 'MatoLoop'
      this.loaded = false
      this.loadFailed = false
      this.loading = false
      Object.assign(this, defaultSettings())
      this.settingsLoaded = false
      this.settingsLoadFailed = false
      this.settingsLoading = false
      this.settingsSaving = false
    },
    assignIdentity(identity, invalidateRequests = true) {
      if (invalidateRequests) this.requestRevision += 1
      this.displayName = String(identity?.displayName || 'MatoLoop')
      this.loaded = true
      this.loadFailed = false
    },
    assignSettings(settings) {
      this.scope = settings?.scope === 'instance' ? 'instance' : 'personal'
      this.configuredDisplayName = typeof settings?.configuredDisplayName === 'string'
        ? settings.configuredDisplayName
        : null
      this.effectiveDisplayName = String(settings?.effectiveDisplayName || 'MatoLoop')
      this.personalDisplayNamesEnabled = settings?.personalDisplayNamesEnabled !== false
      this.personalPersonasEnabled = settings?.personalPersonasEnabled !== false
      this.defaultPersonaId = settings?.defaultPersonaId || null
      this.effectiveDefaultPersonaId = settings?.effectiveDefaultPersonaId || null
      this.personas = Array.isArray(settings?.personas) ? settings.personas : []
      this.settingsLoaded = true
      this.settingsLoadFailed = false
      this.assignIdentity({ displayName: this.effectiveDisplayName })
    },
    async fetchIdentity(options) {
      const revision = ++this.requestRevision
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/agent-identity', options)
        if (revision === this.requestRevision) this.assignIdentity(data, false)
        return { displayName: this.displayName }
      } catch (error) {
        if (revision === this.requestRevision) this.loadFailed = true
        throw error
      } finally {
        if (revision === this.requestRevision) this.loading = false
      }
    },
    async fetchSettings(scope = 'personal') {
      const revision = ++this.settingsRevision
      this.scope = scope === 'instance' ? 'instance' : 'personal'
      this.settingsLoaded = false
      this.settingsLoading = true
      this.settingsLoadFailed = false
      try {
        const { data } = await api.get('/agent-identity/settings', scopeConfig(this.scope))
        if (revision === this.settingsRevision) this.assignSettings(data)
        return data
      } catch (error) {
        if (revision === this.settingsRevision) this.settingsLoadFailed = true
        throw error
      } finally {
        if (revision === this.settingsRevision) this.settingsLoading = false
      }
    },
    async saveDisplayName(displayName) {
      return this._mutate(() => api.patch(
        '/agent-identity/settings/display-name',
        { displayName },
        scopeConfig(this.scope)
      ))
    },
    async resetDisplayName() {
      return this._mutate(() => api.delete('/agent-identity/settings/display-name'))
    },
    async setPersonalDisplayNamesEnabled(enabled) {
      return this._mutate(() => api.patch(
        '/agent-identity/settings/personal-display-names', { enabled }
      ))
    },
    async setPersonalPersonasEnabled(enabled) {
      return this._mutate(() => api.patch(
        '/agent-identity/settings/personal-personas', { enabled }
      ))
    },
    async createPersona(payload) {
      return this._mutate(() => api.post(
        '/agent-identity/personas', payload, scopeConfig(this.scope)
      ))
    },
    async updatePersona(id, payload) {
      return this._mutate(() => api.put(
        `/agent-identity/personas/${id}`, payload, scopeConfig(this.scope)
      ))
    },
    async deletePersona(id) {
      return this._mutate(() => api.delete(
        `/agent-identity/personas/${id}`, scopeConfig(this.scope)
      ))
    },
    async setDefaultPersona(personaId) {
      return this._mutate(() => api.patch(
        '/agent-identity/personas/default', { personaId }, scopeConfig(this.scope)
      ))
    },
    async _mutate(request) {
      const revision = ++this.settingsRevision
      this.settingsSaving = true
      try {
        const { data } = await request()
        if (revision === this.settingsRevision) this.assignSettings(data)
        return data
      } finally {
        if (revision === this.settingsRevision) this.settingsSaving = false
      }
    }
  }
})
