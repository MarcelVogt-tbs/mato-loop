import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn()
  }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useProviderStore } from '../src/stores/providers'

describe('provider store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('builds options from the effective provider and model state', () => {
    const store = useProviderStore()
    store.providers = [
      { id: 'p1', name: 'Aktiv', enabled: true, adapterType: 'openai-compatible' },
      { id: 'p2', name: 'Aus', enabled: false, adapterType: 'openai-compatible' }
    ]
    store.models = [
      { id: 'm1', providerId: 'p1', name: 'Alpha', modelId: 'alpha', enabled: true, availability: 'available' },
      { id: 'm2', providerId: 'p2', name: 'Beta', modelId: 'beta', enabled: true, availability: 'available' },
      { id: 'm3', providerId: 'p1', name: 'Alt', modelId: 'old', enabled: true, availability: 'missing' }
    ]

    expect(store.modelOptions).toEqual([{
      label: 'Alpha · Aktiv',
      value: 'm1',
      trustLevel: 'public',
      trustIcon: 'public',
      disable: false
    }])
  })

  it('offers only effective tool-capable chat models for skill creation', () => {
    const store = useProviderStore()
    store.providers = [
      { id: 'active', name: 'Aktiv', enabled: true },
      { id: 'disabled', name: 'Aus', enabled: false }
    ]
    store.models = [
      { id: 'tools', providerId: 'active', name: 'Tools', enabled: true, supportsTools: true },
      { id: 'unknown', providerId: 'active', name: 'Unbekannt', enabled: true },
      { id: 'no-tools', providerId: 'active', name: 'Ohne Tools', enabled: true, supportsTools: false },
      { id: 'disabled-model', providerId: 'disabled', name: 'Aus', enabled: true, supportsTools: true },
      { id: 'image', providerId: 'active', name: 'Bild', enabled: true, supportsTools: true, capabilities: ['image_generation'] }
    ]

    expect(store.skillCreationModelOptions.map((option) => option.value)).toEqual(['tools'])
  })

  it('keeps media models out of chat selectors and filters options by capability', () => {
    const store = useProviderStore()
    store.providers = [{ id: 'p1', name: 'Lemonade', enabled: true, adapterType: 'lemonade' }]
    store.models = [
      { id: 'legacy', providerId: 'p1', name: 'Legacy Chat', enabled: true },
      { id: 'empty', providerId: 'p1', name: 'Ohne Fähigkeit', enabled: true, capabilities: [] },
      { id: 'image', providerId: 'p1', name: 'Bild', enabled: true, capabilities: ['image_generation'], executableCapabilities: ['image_generation'] },
      { id: 'unsupported', providerId: 'p1', name: 'Nicht ausführbar', enabled: true, capabilities: ['image_generation'], executableCapabilities: [] },
      { id: 'speech', providerId: 'p1', name: 'Sprache', enabled: false, capabilities: ['speech_generation'] }
    ]

    expect(store.modelOptions.map((option) => option.value)).toEqual(['legacy'])
    expect(store.selectionOptions(null).map((option) => option.value)).toEqual(['legacy'])
    expect(store.capabilityOptions('image_generation').map((option) => option.value)).toEqual(['image'])
    expect(store.capabilityOptions('speech_generation')).toEqual([])
    expect(store.selectedModelState('image')).toMatchObject({
      available: false,
      message: 'Das Modell dieser Session unterstützt keinen Chat.'
    })
  })

  it('uses canonical input modalities for vision models with legacy fallback', () => {
    const store = useProviderStore()
    store.providers = [{ id: 'p1', name: 'Lokal', enabled: true }]
    store.models = [
      { id: 'canonical', providerId: 'p1', name: 'Canonical', enabled: true, inputModalities: ['text', 'image'], supportsImageInput: false },
      { id: 'text', providerId: 'p1', name: 'Text', enabled: true, inputModalities: ['text'], supportsImageInput: true },
      { id: 'legacy', providerId: 'p1', name: 'Legacy', enabled: true, supportsImageInput: true }
    ]

    expect(store.visionModelOptions.map((option) => option.value)).toEqual(['canonical', 'legacy'])
  })

  it('offers native video and frame-analysis models', () => {
    const store = useProviderStore()
    store.codex = { authenticated: true }
    store.providers = [
      { id: 'chat', name: 'vLLM', enabled: true, adapterType: 'openai-compatible' },
      { id: 'responses', name: 'Responses', enabled: true, adapterType: 'openai-compatible', config: { apiType: 'responses' } },
      { id: 'lemonade', name: 'Lemonade', enabled: true, adapterType: 'lemonade' },
      { id: 'codex', name: 'GPT-5.6', enabled: true, adapterType: 'openai-codex' }
    ]
    store.models = store.providers.map((provider) => ({
      id: provider.id,
      providerId: provider.id,
      name: provider.name,
      enabled: true,
      capabilities: ['chat'],
      inputModalities: ['text', 'video']
    }))
    store.models.find((model) => model.id === 'codex').inputModalities = ['text', 'image']

    expect(store.videoModelOptions.map((option) => option.value)).toEqual(['chat', 'codex'])
  })

  it('keeps existing models visible while sync is pending and reloads afterward', async () => {
    const store = useProviderStore()
    store.providers = [{ id: 'p1', name: 'Lokal', enabled: true, adapterType: 'openai-compatible' }]
    store.models = [{ id: 'old', providerId: 'p1', name: 'Bestehend', enabled: true }]
    let finishSync
    api.post.mockReturnValue(new Promise((resolve) => { finishSync = resolve }))
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith('/codex/status')
      ? { authenticated: false }
      : {
          providers: store.providers,
          models: [{ id: 'new', providerId: 'p1', name: 'Neu', enabled: true }],
          defaultModelId: 'new'
        } }))

    const syncing = store.syncModels('p1')
    expect(store.models.map((item) => item.id)).toEqual(['old'])
    finishSync({ data: { status: 'ok' } })
    await syncing

    expect(api.post).toHaveBeenCalledWith(
      '/providers/configuration/p1/models/sync',
      null,
      { params: { scope: 'instance' } }
    )
    expect(store.models.map((item) => item.id)).toEqual(['new'])
  })

  it('keeps successfully loaded providers when the Codex status fails', async () => {
    const store = useProviderStore()
    api.get.mockImplementation((url) => url.endsWith('/codex/status')
      ? Promise.reject(new Error('Codex status unavailable'))
      : Promise.resolve({
          data: {
            enabled: true,
            providers: [{ id: 'p1', name: 'Lokal', enabled: true }],
            models: [{ id: 'm1', providerId: 'p1', name: 'Alpha', enabled: true }],
            defaultModelId: 'm1'
          }
        }))

    await expect(store.fetchAll()).resolves.toBeUndefined()

    expect(store.providers.map((provider) => provider.id)).toEqual(['p1'])
    expect(store.models.map((model) => model.id)).toEqual(['m1'])
    expect(store.defaultModelId).toBe('m1')
    expect(store.lastLoadComplete).toBe(false)
    expect(store.loading).toBe(false)
  })

  it('does not let an older fetch overwrite a mutation payload', async () => {
    const requests = []
    api.get.mockImplementation((url) => new Promise((resolve) => {
      requests.push({ url, resolve })
    }))
    api.patch.mockResolvedValue({
      data: {
        providers: [{ id: 'latest', enabled: true }],
        models: [{ id: 'latest-model', providerId: 'latest', enabled: true }]
      }
    })
    const store = useProviderStore()

    const loading = store.fetchAll()
    await store.setProviderEnabled('latest', true)
    requests.find(({ url }) => url === '/providers').resolve({
      data: { providers: [{ id: 'stale' }], models: [] }
    })
    requests.find(({ url }) => url.endsWith('/codex/status')).resolve({
      data: { authenticated: false }
    })
    await loading

    expect(store.providers.map((provider) => provider.id)).toEqual(['latest'])
    expect(store.models.map((model) => model.id)).toEqual(['latest-model'])
    expect(store.loading).toBe(false)
  })

  it('does not let an older provider response overwrite the latest load', async () => {
    const requests = []
    api.get.mockImplementation((url) => new Promise((resolve) => {
      requests.push({ url, resolve })
    }))
    const store = useProviderStore()

    const firstLoad = store.fetchAll()
    const secondLoad = store.fetchAll()
    const secondRequests = requests.slice(2)
    secondRequests.find(({ url }) => url === '/providers').resolve({
      data: { providers: [{ id: 'latest' }], models: [], defaultModelId: null }
    })
    secondRequests.find(({ url }) => url.endsWith('/codex/status')).resolve({
      data: { authenticated: true }
    })
    await secondLoad
    requests[0].resolve({
      data: { providers: [{ id: 'stale' }], models: [], defaultModelId: null }
    })
    requests[1].resolve({ data: { authenticated: false } })
    await firstLoad

    expect(store.providers.map((provider) => provider.id)).toEqual(['latest'])
    expect(store.codex.authenticated).toBe(true)
    expect(store.loading).toBe(false)
  })

  it('does not apply a disabled-personal early response after the scope changed', async () => {
    const requests = []
    api.get.mockImplementation((url, config) => new Promise((resolve) => {
      requests.push({ url, scope: config?.params?.scope, resolve })
    }))
    const store = useProviderStore()
    store.selectScope('personal')

    const personalLoad = store.fetchAll()
    store.selectScope('instance')
    const instanceLoad = store.fetchAll()
    requests.find((request) => request.scope === 'instance' && request.url === '/providers')
      .resolve({ data: { providers: [{ id: 'latest' }], models: [] } })
    requests.find((request) => request.url.endsWith('/codex/status')).resolve({
      data: { authenticated: false }
    })
    await instanceLoad
    requests[0].resolve({
      data: { enabled: true, runtimeEnabled: true, personalProvidersEnabled: false }
    })
    await personalLoad

    expect(store.scope).toBe('instance')
    expect(store.providers.map((provider) => provider.id)).toEqual(['latest'])
    expect(store.loading).toBe(false)
  })

  it('saves model configuration and enabled state atomically', async () => {
    const store = useProviderStore()
    store.providers = [{ id: 'p1', name: 'Lokal', enabled: true, adapterType: 'openai-compatible' }]
    store.models = [{ id: 'm1', providerId: 'p1', modelId: 'alpha', name: 'Alpha', enabled: true }]
    api.put.mockResolvedValue({ data: {} })
    api.patch.mockResolvedValue({ data: {} })
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith('/codex/status')
      ? { authenticated: false }
      : { providers: store.providers, models: store.models, defaultModelId: null } }))

    await store.updateModel('m1', { model_id: 'alpha', name: 'Alpha', enabled: false })

    expect(api.put).toHaveBeenCalledWith(
      '/providers/models/m1',
      { model_id: 'alpha', name: 'Alpha', enabled: false },
      { params: { scope: 'instance' } }
    )
    expect(api.patch).not.toHaveBeenCalled()
  })

  it('uses a successful model mutation response immediately', async () => {
    const store = useProviderStore()
    api.put.mockResolvedValue({
      data: {
        enabled: true,
        providers: [{ id: 'p1', enabled: true }],
        models: [{ id: 'm1', webfetchPolicy: 'auto_public' }],
        defaultModelId: 'm1'
      }
    })

    await store.updateModel('m1', { model_id: 'alpha' })

    expect(store.models[0].webfetchPolicy).toBe('auto_public')
    expect(api.get).not.toHaveBeenCalled()
  })

  it('saves all default models atomically and applies the complete response', async () => {
    const store = useProviderStore()
    api.put.mockResolvedValue({
      data: {
        enabled: true,
        providers: [{ id: 'p1', name: 'Lokal', enabled: true }],
        models: [
          { id: 'agent', providerId: 'p1', enabled: true },
          { id: 'compact', providerId: 'p1', enabled: true }
        ],
        defaultModelId: 'agent',
        compactionModelId: 'compact',
        titleGenerationModelId: null,
        titleVisionFallbackEnabled: false,
        dreamingModelId: 'compact',
        visionModelId: 'vision',
        videoModelId: 'video',
        imageGenerationModelId: 'image',
        speechGenerationModelId: null,
        audioGenerationModelId: 'audio',
        audioTranscriptionModelId: 'transcription',
        skillCreationModelId: 'compact'
      }
    })

    await store.setDefaultModels({
      defaultModelId: 'agent',
      compactionModelId: 'compact',
      titleGenerationModelId: null,
      titleVisionFallbackEnabled: false,
      dreamingModelId: 'compact',
      visionModelId: 'vision',
      videoModelId: 'video',
      imageGenerationModelId: 'image',
      speechGenerationModelId: null,
      audioGenerationModelId: 'audio',
      audioTranscriptionModelId: 'transcription',
      skillCreationModelId: 'compact'
    })

    expect(api.put).toHaveBeenCalledTimes(1)
    expect(api.put).toHaveBeenCalledWith('/providers/default-models', {
      default_model_id: 'agent',
      compaction_model_id: 'compact',
      title_generation_model_id: null,
      title_vision_fallback_enabled: false,
      dreaming_model_id: 'compact',
      vision_model_id: 'vision',
      video_model_id: 'video',
      image_generation_model_id: 'image',
      speech_generation_model_id: null,
      audio_generation_model_id: 'audio',
      audio_transcription_model_id: 'transcription',
      skill_creation_model_id: 'compact'
    }, { params: { scope: 'instance' } })
    expect(store.defaultModelId).toBe('agent')
    expect(store.compactionModelId).toBe('compact')
    expect(store.titleGenerationModelId).toBeNull()
    expect(store.titleVisionFallbackEnabled).toBe(false)
    expect(store.dreamingModelId).toBe('compact')
    expect(store.visionModelId).toBe('vision')
    expect(store.videoModelId).toBe('video')
    expect(store.imageGenerationModelId).toBe('image')
    expect(store.speechGenerationModelId).toBeNull()
    expect(store.audioGenerationModelId).toBe('audio')
    expect(store.audioTranscriptionModelId).toBe('transcription')
    expect(store.skillCreationModelId).toBe('compact')
  })

  it('creates and updates Lemonade providers through the configured endpoints', async () => {
    const store = useProviderStore()
    const payload = {
      name: 'Lemonade lokal',
      base_url: 'http://lemonade-host:13305/v1',
      api_key: null,
      remove_api_key: false,
      trust_level: 'private',
      pseudonymization_enabled: false,
      memory_policy: 'inherit'
    }
    const providerPayload = {
      enabled: true,
      providers: [{
        id: 'lemonade',
        name: payload.name,
        adapterType: 'lemonade',
        config: { baseUrl: payload.base_url }
      }],
      models: [],
      defaultModelId: null
    }
    api.post.mockResolvedValue({ data: { ...providerPayload, providerId: 'lemonade' } })
    api.put.mockResolvedValue({ data: providerPayload })
    api.get.mockImplementation((url) => Promise.resolve({
      data: url.endsWith('/codex/status') ? { authenticated: false } : providerPayload
    }))

    await expect(store.createLemonade(payload)).resolves.toBe('lemonade')
    await store.updateLemonade('lemonade', payload)

    expect(api.post).toHaveBeenCalledWith(
      '/providers/lemonade', payload, { params: { scope: 'instance' } }
    )
    expect(api.put).toHaveBeenCalledWith(
      '/providers/configuration/lemonade', payload, { params: { scope: 'instance' } }
    )
  })

  it('keeps public and private models selectable', () => {
    const store = useProviderStore()
    store.providers = [{ id: 'p1', name: 'Lokal', enabled: true, adapterType: 'openai-compatible' }]
    store.models = [
      { id: 'public', providerId: 'p1', name: 'Öffentlich', enabled: true, trustLevel: 'private', effectiveTrustLevel: 'public' },
      { id: 'private', providerId: 'p1', name: 'Privat', enabled: true, trustLevel: null, effectiveTrustLevel: 'private' }
    ]

    expect(store.selectionOptions('public')).toEqual([
      { label: 'Öffentlich · Lokal', value: 'public', trustLevel: 'public', trustIcon: 'public', disable: false },
      { label: 'Privat · Lokal', value: 'private', trustLevel: 'private', trustIcon: 'lock', disable: false }
    ])
    expect(store.selectedModelState('public')).toMatchObject({
      available: true,
      message: ''
    })
  })

  it('keeps an unavailable default visible without offering it as a new selection', () => {
    const store = useProviderStore()
    store.providers = [{ id: 'p1', name: 'Lokal', enabled: true, adapterType: 'openai-compatible' }]
    store.models = [
      { id: 'available', providerId: 'p1', modelId: 'new', name: 'Neu', enabled: true },
      { id: 'stored', providerId: 'p1', modelId: 'old', name: 'Alt', enabled: false, effectiveTrustLevel: 'private' }
    ]

    expect(store.modelOptions.map((option) => option.value)).toEqual(['available'])
    expect(store.selectionOptions('stored')).toEqual([
      { label: 'Alt · Lokal', value: 'stored', trustLevel: 'private', trustIcon: 'lock', disable: true },
      { label: 'Neu · Lokal', value: 'available', trustLevel: 'public', trustIcon: 'public', disable: false }
    ])
  })

  it('can re-enable the global model runtime', async () => {
    const store = useProviderStore()
    store.enabled = false
    api.patch.mockResolvedValue({ data: {} })
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith('/codex/status')
      ? { authenticated: false }
      : { enabled: true, providers: [], models: [], defaultModelId: null } }))

    await store.setAiEnabled(true)

    expect(api.patch).toHaveBeenCalledWith(
      '/providers/enabled', { enabled: true }, { params: { scope: 'instance' } }
    )
    expect(store.enabled).toBe(true)
  })

  it('updates the common provider policy', async () => {
    const store = useProviderStore()
    api.patch.mockResolvedValue({ data: { enabled: true, providers: [], models: [] } })

    await store.setProviderPolicy('p1', {
      trustLevel: 'private',
      pseudonymizationEnabled: true,
      memoryPolicy: 'deny'
    })

    expect(api.patch).toHaveBeenCalledWith(
      '/providers/configuration/p1/policy',
      {
        trustLevel: 'private',
        pseudonymizationEnabled: true,
        memoryPolicy: 'deny'
      },
      { params: { scope: 'instance' } }
    )
  })

  it('refreshes and synchronizes models after Codex login', async () => {
    const store = useProviderStore()
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith('/codex/status')
      ? { authenticated: true }
      : {
          providers: [{ id: 'codex', adapterType: 'openai-codex', enabled: true }],
          models: [],
          defaultModelId: null
        } }))
    api.post.mockResolvedValue({
      data: {
        providers: [{ id: 'codex', adapterType: 'openai-codex', enabled: true }],
        models: [{ id: 'model', providerId: 'codex', modelId: 'openai-codex/gpt-test' }],
        defaultModelId: null
      }
    })

    await store.refreshAfterCodexLogin('codex')

    expect(api.post).toHaveBeenCalledWith(
      '/providers/configuration/codex/models/sync',
      null,
      { params: { scope: 'instance' } }
    )
    expect(store.models[0].modelId).toBe('openai-codex/gpt-test')
  })

  it('loads personal configuration only after the safe policy check', async () => {
    const store = useProviderStore()
    store.selectScope('personal')
    api.get.mockImplementation((url) => {
      if (url.endsWith('/codex/status')) return Promise.resolve({ data: { authenticated: false } })
      if (url === '/providers/personal-policy') return Promise.resolve({
        data: { enabled: true, runtimeEnabled: true, personalProvidersEnabled: true }
      })
      return Promise.resolve({
        data: {
          enabled: true,
          providers: [{ id: 'personal-provider', name: 'Persönlich', enabled: true }],
          models: [{ id: 'personal-model', providerId: 'personal-provider', enabled: true }],
          availableProviders: [
            { id: 'instance-provider', name: 'Instanz', enabled: true },
            { id: 'personal-provider', name: 'Persönlich', enabled: true }
          ],
          availableModels: [
            { id: 'instance-model', providerId: 'instance-provider', enabled: true },
            { id: 'personal-model', providerId: 'personal-provider', enabled: true }
          ]
        }
      })
    })

    await store.fetchAll()

    expect(store.scope).toBe('personal')
    expect(store.providers.map((provider) => provider.id)).toEqual(['personal-provider'])
    expect(store.models.map((model) => model.id)).toEqual(['personal-model'])
    expect(store.modelOptions.map((option) => option.value)).toEqual(['instance-model', 'personal-model'])
    expect(api.get).toHaveBeenCalledWith('/providers/personal-policy')
    expect(api.get).toHaveBeenCalledWith('/providers', { params: { scope: 'personal' } })
    expect(api.get).not.toHaveBeenCalledWith('/providers', { params: { scope: 'instance' } })
  })

  it('loads the backend effective catalog without scoped raw reads', async () => {
    api.get.mockResolvedValue({
      data: {
        enabled: true,
        providers: [{ id: 'effective', name: 'Effektiv', enabled: true }],
        models: [{ id: 'effective-model', providerId: 'effective', enabled: true }]
      }
    })
    const store = useProviderStore()

    await store.fetchEffective()

    expect(store.modelOptions.map((option) => option.value)).toEqual(['effective-model'])
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/providers')
  })
})
