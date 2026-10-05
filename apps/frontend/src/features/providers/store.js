import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useAuthStore } from 'stores/auth'
import {
  isModelEffective,
  modelOption,
  modelSupportsCapability,
  selectedModelState
} from 'src/utils/provider-models'

function assignProviderPayload(store, payload) {
  if (!payload || !Array.isArray(payload.providers) || !Array.isArray(payload.models)) return
  store.providers = payload.providers
  store.models = payload.models
  store.modelDefaults = payload.modelDefaults || {}
  store.availableProviders = payload.availableProviders ?? payload.providers
  store.availableModels = payload.availableModels ?? payload.models
  if ('personalProvidersEnabled' in payload) {
    store.personalProvidersEnabled = payload.personalProvidersEnabled === true
  }
}

function scopeConfig(scope, config = {}) {
  return { ...config, params: { ...(config.params || {}), scope } }
}

function assignPersonalPolicy(store, policy) {
  store.personalProvidersEnabled = policy?.personalProvidersEnabled === true
  store.personalPolicyLoaded = true
}

function effectiveModelOptions(state, capability = 'chat', requiresTools = false) {
  const models = state.availableModels || state.models
  const providers = state.availableProviders || state.providers
  return models.flatMap((model) => {
    const provider = providers.find((item) => item.id === model.providerId)
    return isModelEffective(model, provider, state.codex)
      && modelSupportsCapability(model, capability)
      && (
        capability === 'chat'
        || !Array.isArray(model.executableCapabilities)
        || model.executableCapabilities.includes(capability)
      )
      ? [modelOption(model, provider, requiresTools && model.supportsTools === false)]
      : []
  })
}

function effectiveVisionModelOptions(state) {
  const models = state.availableModels || state.models
  const providers = state.availableProviders || state.providers
  return models.flatMap((model) => {
    const provider = providers.find((item) => item.id === model.providerId)
    return isModelEffective(model, provider, state.codex)
      && modelSupportsCapability(model, 'chat')
      && (Array.isArray(model.inputModalities)
        ? model.inputModalities.includes('image')
        : model.supportsImageInput === true)
      ? [modelOption(model, provider)]
      : []
  })
}

function effectiveVideoModelOptions(state) {
  const models = state.availableModels || state.models
  const providers = state.availableProviders || state.providers
  return models.flatMap((model) => {
    const provider = providers.find((item) => item.id === model.providerId)
    const inputModalities = Array.isArray(model.inputModalities) ? model.inputModalities : []
    const supportsFrames = inputModalities.includes('image') || model.supportsImageInput === true
    const supportsNativeVideo = inputModalities.includes('video')
      && provider?.adapterType === 'openai-compatible'
      && (provider.config?.apiType || provider.config?.api_type || 'chat_completions') === 'chat_completions'
    return isModelEffective(model, provider, state.codex)
      && modelSupportsCapability(model, 'chat')
      && (supportsNativeVideo || supportsFrames)
      ? [modelOption(model, provider)]
      : []
  })
}

function effectiveAudioAnalysisModelOptions(state) {
  const models = state.availableModels || state.models
  const providers = state.availableProviders || state.providers
  return models.flatMap((model) => {
    const provider = providers.find((item) => item.id === model.providerId)
    return isModelEffective(model, provider, state.codex)
      && modelSupportsCapability(model, 'chat')
      && model.inputModalities?.includes('audio')
      && provider?.adapterType === 'openai-compatible'
      && (provider.config?.apiType || provider.config?.api_type || 'chat_completions') === 'chat_completions'
      ? [modelOption(model, provider)]
      : []
  })
}

async function mutateAndRefresh(store, request) {
  const scope = store.scope
  const response = await request()
  if (store.scope !== scope) return response
  const payload = response?.data
  if (store.scope === 'personal') {
    await store.fetchAll().catch(() => {})
  } else if (payload && Array.isArray(payload.providers) && Array.isArray(payload.models)) {
    store.loadVersion += 1
    store.loading = false
    assignProviderPayload(store, payload)
  } else {
    await store.fetchAll().catch(() => {})
  }
  return response
}

export const useProviderStore = defineStore('providers', {
  state: () => ({
    providers: [],
    models: [],
    modelDefaults: {},
    availableProviders: null,
    availableModels: null,
    scope: 'instance',
    personalProvidersEnabled: false,
    personalPolicyLoaded: false,
    codex: {},
    loading: false,
    loadVersion: 0,
    lastLoadComplete: false
  }),
  getters: {
    modelOptions: (state) => effectiveModelOptions(state),
    capabilityOptions: (state) => (capability) => effectiveModelOptions(state, capability),
    visionModelOptions: (state) => effectiveVisionModelOptions(state),
    videoModelOptions: (state) => effectiveVideoModelOptions(state),
    audioAnalysisModelOptions: (state) => effectiveAudioAnalysisModelOptions(state),
    skillCreationModelOptions: (state) => effectiveModelOptions(state)
      .filter((option) => (state.availableModels || state.models)
        .find((model) => model.id === option.value)?.supportsTools === true),
    selectionOptions: (state) => (modelId, requiresTools = false, capability = 'chat') => {
      const options = effectiveModelOptions(state, capability, requiresTools)
      if (!modelId || options.some((option) => option.value === modelId)) return options
      const selected = selectedModelState(
        modelId,
        state.availableModels || state.models,
        state.availableProviders || state.providers,
        state.codex,
        capability
      )
      const models = state.availableModels || state.models
      const providers = state.availableProviders || state.providers
      const selectedModel = models.find((model) => model.id === modelId)
      if (selectedModel) {
        const provider = providers.find((item) => item.id === selectedModel.providerId)
        return [modelOption(selectedModel, provider, true), ...options]
      }
      return [{ label: selected.label, value: modelId, disable: true, trustLevel: 'public', trustIcon: 'public' }, ...options]
    },
    selectedModelState: (state) => (modelId, capability = 'chat') => selectedModelState(
      modelId,
      state.availableModels || state.models,
      state.availableProviders || state.providers,
      state.codex,
      capability
    )
  },
  actions: {
    async fetchPersonalPolicy() {
      try {
        const { data } = await api.get('/providers/personal-policy')
        assignPersonalPolicy(this, data)
        return data
      } catch (error) {
        this.personalPolicyLoaded = false
        throw error
      }
    },
    selectScope(scope) {
      const next = scope === 'personal' ? 'personal' : 'instance'
      if (next === this.scope) return
      this.scope = next
      this.loadVersion += 1
      this.loading = false
      this.lastLoadComplete = false
    },
    async fetchAll() {
      this.loadVersion += 1
      const loadVersion = this.loadVersion
      const scope = this.scope
      this.loading = true
      this.lastLoadComplete = false
      try {
        const policy = scope === 'personal'
          ? (await api.get('/providers/personal-policy')).data
          : null
        if (this.loadVersion !== loadVersion || this.scope !== scope) return
        if (policy) assignPersonalPolicy(this, policy)
        if (scope === 'personal' && !this.personalProvidersEnabled && !useAuthStore().isAdmin) {
          assignProviderPayload(this, { providers: [], models: [] })
          assignPersonalPolicy(this, policy)
          this.lastLoadComplete = true
          return
        }
        const [providers, codex] = await Promise.allSettled([
          api.get('/providers', scopeConfig(scope)),
          api.get('/providers/codex/status', scopeConfig(scope, { suppressErrorToast: true }))
        ])
        if (this.loadVersion !== loadVersion || this.scope !== scope) return
        if (providers.status === 'fulfilled') {
          assignProviderPayload(this, providers.value.data)
          if (policy) assignPersonalPolicy(this, policy)
        }
        if (codex.status === 'fulfilled') this.codex = codex.value.data
        this.lastLoadComplete = providers.status === 'fulfilled' && codex.status === 'fulfilled'
        if (providers.status === 'rejected') throw providers.reason
      } finally {
        if (this.loadVersion === loadVersion && this.scope === scope) this.loading = false
      }
    },
    async fetchEffective() {
      this.loadVersion += 1
      const loadVersion = this.loadVersion
      this.loading = true
      this.lastLoadComplete = false
      try {
        const effective = (await api.get('/providers')).data
        if (this.loadVersion !== loadVersion) return
        assignProviderPayload(this, effective)
        this.lastLoadComplete = true
      } finally {
        if (this.loadVersion === loadVersion) this.loading = false
      }
    },
    async createProvider(type, payload) {
      const previousIds = new Set(this.providers.map((provider) => provider.id))
      const { data } = await api.post(`/providers/${type}`, payload, scopeConfig(this.scope))
      assignProviderPayload(this, data)
      await this.fetchAll()
      return data?.providerId
        || data?.provider?.id
        || (data?.id && !Array.isArray(data.providers) ? data.id : null)
        || this.providers.find((provider) => !previousIds.has(provider.id))?.id
        || this.providers.find((provider) => (
          provider.name === payload.name
          && (provider.config?.baseUrl || provider.config?.base_url) === payload.base_url.replace(/\/$/, '')
        ))?.id
        || null
    },
    async updateProvider(id, payload) {
      await api.put(`/providers/configuration/${id}`, payload, scopeConfig(this.scope))
      await this.fetchAll()
    },
    async setProviderEnabled(id, enabled) {
      await mutateAndRefresh(this, () => api.patch(`/providers/configuration/${id}/enabled`, { enabled }, scopeConfig(this.scope)))
    },
    async setProviderPolicy(id, { trustLevel, pseudonymizationEnabled, memoryPolicy }) {
      await mutateAndRefresh(this, () => api.patch(
        `/providers/configuration/${id}/policy`,
        { trustLevel, pseudonymizationEnabled, memoryPolicy },
        scopeConfig(this.scope)
      ))
    },
    async setPersonalProvidersEnabled(enabled) {
      await api.patch('/providers/personal-enabled', { enabled }, scopeConfig('instance'))
      this.personalProvidersEnabled = enabled
      this.personalPolicyLoaded = true
    },
    async syncModels(id) {
      return (await mutateAndRefresh(this, () => api.post(`/providers/configuration/${id}/models/sync`, null, scopeConfig(this.scope))))?.data
    },
    async createModel(id, payload) {
      return (await mutateAndRefresh(
        this,
        () => api.post(`/providers/configuration/${id}/models`, payload, scopeConfig(this.scope))
      ))?.data
    },
    async updateModel(id, payload) {
      return (await mutateAndRefresh(
        this,
        () => api.put(`/providers/models/${id}`, payload, scopeConfig(this.scope))
      ))?.data
    },
    async setModelEnabled(id, enabled) {
      await mutateAndRefresh(this, () => api.patch(`/providers/models/${id}/enabled`, { enabled }, scopeConfig(this.scope)))
    },
    async removeModel(id) {
      await mutateAndRefresh(this, () => api.delete(`/providers/models/${id}`, scopeConfig(this.scope)))
    },
    async testModel(id) {
      return (await api.post(`/providers/models/${id}/test`, null, scopeConfig(this.scope))).data
    },
    async removeProvider(id) {
      await api.delete(`/providers/configuration/${id}`, scopeConfig(this.scope))
      await this.fetchAll()
    },
    async testProvider(id) {
      const provider = this.providers.find((item) => item.id === id)
      const model = this.models.find((item) => (
        item.providerId === id && isModelEffective(item, provider, this.codex)
        && modelSupportsCapability(item, 'chat')
      ))
      if (!model) return null
      return this.testModel(model.id)
    },
    async startCodexLogin() {
      return (await api.post('/providers/codex/login', null, scopeConfig(this.scope))).data
    },
    async fetchCodexLogin(loginId) {
      return (await api.get(`/providers/codex/login/${loginId}`, scopeConfig(this.scope))).data
    },
    async refreshAfterCodexLogin(providerId) {
      await this.fetchAll()
      const id = providerId
        || this.providers.find((provider) => provider.adapterType === 'openai-codex')?.id
      if (id) await this.syncModels(id)
    },
    async logoutCodex() {
      await api.delete('/providers/codex/logout', scopeConfig(this.scope))
      await this.fetchAll()
    }
  }
})
