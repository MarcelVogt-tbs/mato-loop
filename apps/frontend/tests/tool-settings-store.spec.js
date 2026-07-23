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

  it('loads and saves the complete tool settings contract without changing field names', async () => {
    const response = {
      webFetch: {
        enabled: true,
        defaultPolicy: 'auto_allowed',
        allowedPrivateOrigins: ['http://127.0.0.1:8080']
      },
      generateImage: { policy: 'ask', timeoutSeconds: 600 },
      generateSpeech: { policy: 'auto', timeoutSeconds: 120 },
      generateAudio: { policy: 'disabled', timeoutSeconds: 900 },
      transcribeAudio: { policy: 'ask', timeoutSeconds: 180 }
    }
    api.get.mockResolvedValue({ data: response })
    api.put.mockResolvedValue({ data: response })
    const store = useToolSettingsStore()

    await store.fetchSettings()
    await store.saveSettings(response)

    expect(api.get).toHaveBeenCalledWith('/tools/settings')
    expect(api.put).toHaveBeenCalledWith('/tools/settings', expect.objectContaining(response))
    expect(store.webFetch).toEqual(response.webFetch)
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
        generateImage: { policy: 'disabled', timeoutSeconds: 300 },
        generateSpeech: { policy: 'disabled', timeoutSeconds: 300 },
        generateAudio: { policy: 'disabled', timeoutSeconds: 300 },
        transcribeAudio: { policy: 'disabled', timeoutSeconds: 300 }
      }
    })
    const store = useToolSettingsStore()

    await store.fetchSettings()
    await store.saveSettings({ generateImage: { policy: 'ask' } })

    expect(api.put).toHaveBeenCalledWith('/tools/settings', {
      webFetch: legacyResponse.webFetch,
      generateImage: { policy: 'ask', timeoutSeconds: 300 },
      generateSpeech: { policy: 'disabled', timeoutSeconds: 300 },
      generateAudio: { policy: 'disabled', timeoutSeconds: 300 },
      transcribeAudio: { policy: 'disabled', timeoutSeconds: 300 }
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
