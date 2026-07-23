import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import {
  isModelEffective,
  modelOption,
  selectedModelState
} from 'src/utils/provider-models'

function assignProviderPayload(store, payload) {
  if (!payload || !Array.isArray(payload.providers) || !Array.isArray(payload.models)) return
  store.providers = payload.providers
  store.models = payload.models
  store.defaultModelId = payload.defaultModelId ?? null
  store.compactionModelId = payload.compactionModelId ?? null
  store.titleGenerationModelId = payload.titleGenerationModelId ?? null
  store.dreamingModelId = payload.dreamingModelId ?? null
  store.enabled = payload.enabled !== false
}

async function mutateAndRefresh(store, request) {
  const response = await request()
  const payload = response?.data
  if (payload && Array.isArray(payload.providers) && Array.isArray(payload.models)) {
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
    defaultModelId: null,
    compactionModelId: null,
    titleGenerationModelId: null,
    dreamingModelId: null,
    enabled: true,
    codex: {},
    loading: false
  }),
  getters: {
    modelOptions: (state) => state.models.flatMap((model) => {
      const provider = state.providers.find((item) => item.id === model.providerId)
      return isModelEffective(model, provider, state.codex, state.enabled) ? [modelOption(model, provider)] : []
    }),
    selectionOptions: (state) => (modelId, requiresTools = false) => {
      const options = state.models.flatMap((model) => {
        const provider = state.providers.find((item) => item.id === model.providerId)
        return isModelEffective(model, provider, state.codex, state.enabled)
          ? [modelOption(
              model,
              provider,
              requiresTools && model.supportsTools === false
            )]
          : []
      })
      if (!modelId || options.some((option) => option.value === modelId)) return options
      const selected = selectedModelState(modelId, state.models, state.providers, state.codex)
      const selectedModel = state.models.find((model) => model.id === modelId)
      if (selectedModel) {
        const provider = state.providers.find((item) => item.id === selectedModel.providerId)
        return [modelOption(selectedModel, provider, true), ...options]
      }
      return [{ label: selected.label, value: modelId, disable: true, trustLevel: 'public', trustIcon: 'public' }, ...options]
    },
    selectedModelState: (state) => (modelId) => selectedModelState(
      modelId,
      state.models,
      state.providers,
      state.codex,
      state.enabled
    )
  },
  actions: {
    async fetchAll() {
      this.loading = true
      try {
        const [providers, codex] = await Promise.all([
          api.get('/providers'),
          api.get('/providers/codex/status')
        ])
        assignProviderPayload(this, providers.data)
        this.codex = codex.data
      } finally {
        this.loading = false
      }
    },
    async createOpenAI(payload) {
      const previousIds = new Set(this.providers.map((provider) => provider.id))
      const { data } = await api.post('/providers/openai-compatible', payload)
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
    async updateOpenAI(id, payload) {
      await api.put(`/providers/configuration/${id}`, payload)
      await this.fetchAll()
    },
    async setProviderEnabled(id, enabled) {
      await mutateAndRefresh(this, () => api.patch(`/providers/configuration/${id}/enabled`, { enabled }))
    },
    async setProviderPolicy(id, { trustLevel, pseudonymizationEnabled, memoryPolicy }) {
      await mutateAndRefresh(this, () => api.patch(
        `/providers/configuration/${id}/policy`,
        { trustLevel, pseudonymizationEnabled, memoryPolicy }
      ))
    },
    async setAiEnabled(enabled) {
      await mutateAndRefresh(this, () => api.patch('/providers/enabled', { enabled }))
    },
    async syncModels(id) {
      return (await mutateAndRefresh(this, () => api.post(`/providers/configuration/${id}/models/sync`)))?.data
    },
    async createModel(id, payload) {
      return (await mutateAndRefresh(
        this,
        () => api.post(`/providers/configuration/${id}/models`, payload)
      ))?.data
    },
    async updateModel(id, payload) {
      return (await mutateAndRefresh(
        this,
        () => api.put(`/providers/models/${id}`, payload)
      ))?.data
    },
    async setModelEnabled(id, enabled) {
      await mutateAndRefresh(this, () => api.patch(`/providers/models/${id}/enabled`, { enabled }))
    },
    async removeModel(id) {
      await mutateAndRefresh(this, () => api.delete(`/providers/models/${id}`))
    },
    async testModel(id) {
      return (await api.post(`/providers/models/${id}/test`)).data
    },
    async setDefaultModels({ defaultModelId, compactionModelId, titleGenerationModelId, dreamingModelId }) {
      return (await mutateAndRefresh(this, () => api.put('/providers/default-models', {
        default_model_id: defaultModelId || null,
        compaction_model_id: compactionModelId || null,
        title_generation_model_id: titleGenerationModelId || null,
        dreaming_model_id: dreamingModelId || null
      })))?.data
    },
    async removeProvider(id) {
      await api.delete(`/providers/configuration/${id}`)
      await this.fetchAll()
    },
    async testProvider(id) {
      const provider = this.providers.find((item) => item.id === id)
      const model = this.models.find((item) => (
        item.providerId === id && isModelEffective(item, provider, this.codex, this.enabled)
      ))
      if (!model) return null
      return this.testModel(model.id)
    },
    async startCodexLogin() {
      return (await api.post('/providers/codex/login')).data
    },
    async fetchCodexLogin(loginId) {
      return (await api.get(`/providers/codex/login/${loginId}`)).data
    },
    async refreshAfterCodexLogin(providerId) {
      await this.fetchAll()
      const id = providerId
        || this.providers.find((provider) => provider.adapterType === 'openai-codex')?.id
      if (id) await this.syncModels(id)
    },
    async logoutCodex() {
      await api.delete('/providers/codex/logout')
      await this.fetchAll()
    }
  }
})
