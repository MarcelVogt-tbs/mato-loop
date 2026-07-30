import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useToolSettingsStore } from '../src/stores/tool-settings'

describe('tool settings store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('defaults public webfetch targets to automatic execution', () => {
    const store = useToolSettingsStore()

    expect(store.webFetch.defaultPolicy).toBe('auto_public')
  })

  it('loads and saves the complete tool settings contract without changing field names', async () => {
    const response = {
      groups: [
        {
          id: 'filesystem',
          label: 'Dateisystem',
          icon: 'folder',
          tools: []
        }
      ],
      policies: { 'filesystem.read': 'ask' },
      webSearch: {
        primaryProvider: 'searxng:integration-id',
        fallbackProvider: 'no_key',
        providerOptions: [
          { value: 'no_key', name: 'Websuche ohne API-Key', adapterType: 'ddgs', integrationId: null },
          { value: 'searxng:integration-id', name: 'Intern', adapterType: 'searxng', integrationId: 'integration-id' }
        ]
      },
      webFetch: {
        enabled: true,
        userAgent: 'ConfiguredBrowser/1.0',
        maxResponseMiB: 96,
        defaultPolicy: 'auto_allowed',
        allowedPrivateOrigins: ['http://127.0.0.1:8080']
      },
      generateImage: { policy: 'ask', timeoutSeconds: 600 },
      generateSpeech: { policy: 'auto', timeoutSeconds: 120 },
      generateAudio: { policy: 'disabled', timeoutSeconds: 900 },
      transcribeAudio: { policy: 'ask', timeoutSeconds: 180 },
      updateAgentIdentity: { policy: 'auto' }
    }
    api.get.mockResolvedValue({ data: response })
    api.put.mockResolvedValue({ data: response })
    const store = useToolSettingsStore()

    await store.fetchSettings()
    await store.saveSettings(response)

    expect(api.get).toHaveBeenCalledWith('/tools/settings')
    expect(api.put).toHaveBeenCalledWith('/tools/settings', expect.objectContaining({
      policies: expect.objectContaining({ 'filesystem.read': 'ask' }),
      webSearch: {
        primaryProvider: 'searxng:integration-id',
        fallbackProvider: 'no_key'
      },
      webFetch: response.webFetch
    }))
    expect(store.webSearch).toEqual(response.webSearch)
    expect(store.webFetch).toEqual(response.webFetch)
    expect(store.groups).toEqual(response.groups)
    expect(store.policies).toEqual({ 'filesystem.read': 'ask' })
    expect(store.generateSpeech).toEqual({ policy: 'auto', timeoutSeconds: 120 })
    expect(store.loaded).toBe(true)
  })

  it('uses disabled defaults for missing additive media settings and still sends them all', async () => {
    const legacyResponse = {
      webFetch: { enabled: true, defaultPolicy: 'ask', allowedPrivateOrigins: [] }
    }
    api.get.mockResolvedValue({ data: legacyResponse })
    api.put.mockResolvedValue({
      data: {
        ...legacyResponse,
        webSearch: {
          primaryProvider: 'no_key',
          fallbackProvider: null,
          providerOptions: [
            { value: 'no_key', name: 'Websuche ohne API-Key', adapterType: 'ddgs', integrationId: null }
          ]
        },
        generateImage: { policy: 'disabled', timeoutSeconds: 300 },
        generateSpeech: { policy: 'disabled', timeoutSeconds: 300 },
        generateAudio: { policy: 'disabled', timeoutSeconds: 300 },
        transcribeAudio: { policy: 'disabled', timeoutSeconds: 300 },
        updateAgentIdentity: { policy: 'ask' }
      }
    })
    const store = useToolSettingsStore()

    await store.fetchSettings()
    await store.saveSettings({ generateImage: { policy: 'ask' } })

    expect(api.put).toHaveBeenCalledWith('/tools/settings', {
      policies: { 'media.generate.image': 'ask' },
      webSearch: { primaryProvider: 'no_key', fallbackProvider: null },
      webFetch: {
        ...legacyResponse.webFetch,
        userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        maxResponseMiB: 64
      },
      generateImage: { policy: 'ask', timeoutSeconds: 300 },
      generateSpeech: { policy: 'disabled', timeoutSeconds: 300 },
      generateAudio: { policy: 'disabled', timeoutSeconds: 300 },
      transcribeAudio: { policy: 'disabled', timeoutSeconds: 300 },
      updateAgentIdentity: { policy: 'ask' }
    })
  })

  it('keeps saving disabled after a failed initial load', async () => {
    api.get.mockRejectedValue(new Error('offline'))
    const store = useToolSettingsStore()

    await expect(store.fetchSettings()).rejects.toThrow('offline')

    expect(store.loaded).toBe(false)
    expect(store.loadFailed).toBe(true)
  })
})
