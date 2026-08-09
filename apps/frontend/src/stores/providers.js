import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
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
  store.defaultModelId = payload.defaultModelId ?? null
  store.compactionModelId = payload.compactionModelId ?? null
  store.titleGenerationModelId = payload.titleGenerationModelId ?? null
  store.dreamingModelId = payload.dreamingModelId ?? null
  store.visionModelId = payload.visionModelId ?? null
  store.videoModelId = payload.videoModelId ?? null
  store.imageGenerationModelId = payload.imageGenerationModelId ?? null
  store.speechGenerationModelId = payload.speechGenerationModelId ?? null
  store.audioGenerationModelId = payload.audioGenerationModelId ?? null
  store.audioTranscriptionModelId = payload.audioTranscriptionModelId ?? null
  store.skillCreationModelId = payload.skillCreationModelId ?? null
  store.enabled = payload.enabled !== false
}

function effectiveModelOptions(state, capability = 'chat', requiresTools = false) {
  return state.models.flatMap((model) => {
    const provider = state.providers.find((item) => item.id === model.providerId)
    return isModelEffective(model, provider, state.codex, state.enabled)
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
  return state.models.flatMap((model) => {
    const provider = state.providers.find((item) => item.id === model.providerId)
    return isModelEffective(model, provider, state.codex, state.enabled)
      && modelSupportsCapability(model, 'chat')
      && (Array.isArray(model.inputModalities)
        ? model.inputModalities.includes('image')
        : model.supportsImageInput === true)
      ? [modelOption(model, provider)]
      : []
  })
}

function effectiveVideoModelOptions(state) {
  return state.models.flatMap((model) => {
    const provider = state.providers.find((item) => item.id === model.providerId)
    const inputModalities = Array.isArray(model.inputModalities) ? model.inputModalities : []
    const supportsFrames = inputModalities.includes('image') || model.supportsImageInput === true
    const supportsNativeVideo = inputModalities.includes('video')
      && provider?.adapterType === 'openai-compatible'
      && (provider.config?.apiType || provider.config?.api_type || 'chat_completions') === 'chat_completions'
    return isModelEffective(model, provider, state.codex, state.enabled)
      && modelSupportsCapability(model, 'chat')
      && (supportsNativeVideo || supportsFrames)
      ? [modelOption(model, provider)]
      : []
  })
}

async function mutateAndRefresh(store, request) {
  const response = await request()
  const payload = response?.data
  if (payload && Array.isArray(payload.providers) && Array.isArray(payload.models)) {
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
    defaultModelId: null,
    compactionModelId: null,
    titleGenerationModelId: null,
    dreamingModelId: null,
    visionModelId: null,
    videoModelId: null,
    imageGenerationModelId: null,
    speechGenerationModelId: null,
    audioGenerationModelId: null,
    audioTranscriptionModelId: null,
    skillCreationModelId: null,
    enabled: true,
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
    skillCreationModelOptions: (state) => effectiveModelOptions(state)
      .filter((option) => state.models.find((model) => model.id === option.value)?.supportsTools === true),
    selectionOptions: (state) => (modelId, requiresTools = false, capability = 'chat') => {
      const options = effectiveModelOptions(state, capability, requiresTools)
      if (!modelId || options.some((option) => option.value === modelId)) return options
      const selected = selectedModelState(
        modelId,
        state.models,
        state.providers,
        state.codex,
        state.enabled,
        capability
      )
      const selectedModel = state.models.find((model) => model.id === modelId)
      if (selectedModel) {
        const provider = state.providers.find((item) => item.id === selectedModel.providerId)
        return [modelOption(selectedModel, provider, true), ...options]
      }
      return [{ label: selected.label, value: modelId, disable: true, trustLevel: 'public', trustIcon: 'public' }, ...options]
    },
    selectedModelState: (state) => (modelId, capability = 'chat') => selectedModelState(
      modelId,
      state.models,
      state.providers,
      state.codex,
      state.enabled,
      capability
    )
  },
  actions: {
    async fetchAll() {
      this.loadVersion += 1
      const loadVersion = this.loadVersion
      this.loading = true
      try {
        const [providers, codex] = await Promise.allSettled([
          api.get('/providers'),
          api.get('/providers/codex/status', { suppressErrorToast: true })
        ])
        if (this.loadVersion !== loadVersion) return
        if (providers.status === 'fulfilled') assignProviderPayload(this, providers.value.data)
        if (codex.status === 'fulfilled') this.codex = codex.value.data
        this.lastLoadComplete = providers.status === 'fulfilled' && codex.status === 'fulfilled'
        if (providers.status === 'rejected') throw providers.reason
      } finally {
        if (this.loadVersion === loadVersion) this.loading = false
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
    async createLemonade(payload) {
      const previousIds = new Set(this.providers.map((provider) => provider.id))
      const { data } = await api.post('/providers/lemonade', payload)
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
    async updateLemonade(id, payload) {
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
    async setDefaultModels({
      defaultModelId,
      compactionModelId,
      titleGenerationModelId,
      dreamingModelId,
      visionModelId,
      videoModelId,
      imageGenerationModelId,
      speechGenerationModelId,
      audioGenerationModelId,
      audioTranscriptionModelId,
      skillCreationModelId
    }) {
      return (await mutateAndRefresh(this, () => api.put('/providers/default-models', {
        default_model_id: defaultModelId || null,
        compaction_model_id: compactionModelId || null,
        title_generation_model_id: titleGenerationModelId || null,
        dreaming_model_id: dreamingModelId || null,
        vision_model_id: visionModelId || null,
        video_model_id: videoModelId || null,
        image_generation_model_id: imageGenerationModelId || null,
        speech_generation_model_id: speechGenerationModelId || null,
        audio_generation_model_id: audioGenerationModelId || null,
        audio_transcription_model_id: audioTranscriptionModelId || null,
        skill_creation_model_id: skillCreationModelId || null
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
        && modelSupportsCapability(item, 'chat')
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
