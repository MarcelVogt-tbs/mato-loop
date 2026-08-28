import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), put: vi.fn() }
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
    expect(store.editImage.policy).toBe('ask')
    expect(store.analyzeVideo).toEqual({ policy: 'ask', timeoutSeconds: 1800 })
  })

  it('loads and saves the complete tool settings contract without changing field names', async () => {
    const response = {
      groups: [
        {
          id: 'filesystem',
          label: 'Dateisystem',
          icon: 'folder',
          tools: [
            {
              name: 'read',
              source: { type: 'builtin' },
              actions: [{ capability: 'filesystem.read', configurable: true }]
            },
            {
              name: 'update_user_profile',
              source: { type: 'builtin' },
              actions: [{ capability: 'settings.user_profile.update', configurable: true }]
            },
            {
              name: 'calendar_query_events',
              source: { type: 'integration' },
              actions: [{ capability: 'calendar.read', configurable: true }]
            }
          ]
        }
      ],
      policies: {
        'filesystem.read': 'ask',
        'settings.user_profile.update': 'auto'
      },
      shellPrefixes: { read: [['cat']], write: [['git', 'status']] },
      webFetch: {
        enabled: true,
        userAgent: 'ConfiguredBrowser/1.0',
        maxResponseMiB: 96,
        defaultPolicy: 'auto_allowed',
        allowedPrivateOrigins: ['http://127.0.0.1:8080']
      },
      generateImage: { policy: 'ask', timeoutSeconds: 600 },
      editImage: { policy: 'ask', timeoutSeconds: 450 },
      generateSpeech: { policy: 'auto', timeoutSeconds: 120 },
      generateAudio: { policy: 'disabled', timeoutSeconds: 900 },
      transcribeAudio: { policy: 'ask', timeoutSeconds: 180 },
      analyzeVideo: { policy: 'auto', timeoutSeconds: 1200 },
      updateAgentIdentity: { policy: 'auto' }
    }
    api.get.mockResolvedValue({ data: response })
    api.put.mockResolvedValue({ data: response })
    const store = useToolSettingsStore()

    await store.fetchSettings()
    await store.saveSettings(response)

    expect(api.get).toHaveBeenCalledWith('/tools/settings', { params: { scope: 'instance' } })
    expect(api.put).toHaveBeenCalledWith('/tools/settings', expect.objectContaining({
      policies: expect.objectContaining({
        'filesystem.read': 'ask',
        'media.analyze.video': 'auto',
        'settings.user_profile.update': 'auto'
      }),
      webFetch: response.webFetch,
      shellPrefixes: response.shellPrefixes
    }), { params: { scope: 'instance' } })
    expect(store.webFetch).toEqual(response.webFetch)
    expect(store.groups[0].tools.map((tool) => tool.name)).toEqual(['read', 'update_user_profile'])
    expect(store.policies).toEqual({
      'filesystem.read': 'ask',
      'settings.user_profile.update': 'auto'
    })
    expect(store.shellPrefixes).toEqual(response.shellPrefixes)
    expect(store.generateSpeech).toEqual({ policy: 'auto', timeoutSeconds: 120 })
    expect(store.analyzeVideo).toEqual(response.analyzeVideo)
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
        editImage: { policy: 'ask', timeoutSeconds: 300 },
        generateSpeech: { policy: 'disabled', timeoutSeconds: 300 },
        generateAudio: { policy: 'disabled', timeoutSeconds: 300 },
        transcribeAudio: { policy: 'disabled', timeoutSeconds: 300 },
        analyzeVideo: { policy: 'ask', timeoutSeconds: 1800 },
        updateAgentIdentity: { policy: 'ask' }
      }
    })
    const store = useToolSettingsStore()

    await store.fetchSettings()
    await store.saveSettings({ generateImage: { policy: 'ask' } })

    expect(api.put).toHaveBeenCalledWith('/tools/settings', {
      policies: { 'media.generate.image': 'ask' },
      shellPrefixes: { read: [], write: [] },
      webFetch: {
        ...legacyResponse.webFetch,
        userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        maxResponseMiB: 64
      },
      generateImage: { policy: 'ask', timeoutSeconds: 300 },
      editImage: { policy: 'ask', timeoutSeconds: 300 },
      generateSpeech: { policy: 'disabled', timeoutSeconds: 300 },
      generateAudio: { policy: 'disabled', timeoutSeconds: 300 },
      transcribeAudio: { policy: 'disabled', timeoutSeconds: 300 },
      analyzeVideo: { policy: 'ask', timeoutSeconds: 1800 },
      updateAgentIdentity: { policy: 'ask' }
    }, { params: { scope: 'instance' } })
  })

  it('keeps saving disabled after a failed initial load', async () => {
    api.get.mockRejectedValue(new Error('offline'))
    const store = useToolSettingsStore()

    await expect(store.fetchSettings()).rejects.toThrow('offline')

    expect(store.loaded).toBe(false)
    expect(store.loadFailed).toBe(true)
  })

  it('loads the narrow tool availability contract for chat', async () => {
    api.get.mockResolvedValue({
      data: {
        webFetch: { enabled: false, defaultPolicy: 'ask' },
        transcribeAudio: { policy: 'ask' }
      }
    })
    const store = useToolSettingsStore()

    await store.fetchAvailability()

    expect(api.get).toHaveBeenCalledWith('/tools/availability')
    expect(store.webFetch).toEqual(expect.objectContaining({ enabled: false, defaultPolicy: 'ask' }))
    expect(store.transcribeAudio).toEqual({ policy: 'ask', timeoutSeconds: 300 })
    expect(store.loaded).toBe(true)
  })

  it('keeps integration tools out of the tool settings payload', async () => {
    api.get.mockResolvedValue({
      data: {
        groups: [{
          id: 'calendar',
          tools: [
            {
              name: 'session_read',
              source: { type: 'builtin' },
              actions: [{ capability: 'session.read', configurable: true }]
            },
            {
              name: 'calendar_query_events',
              source: { type: 'integration' },
              actions: [{ capability: 'calendar.read', configurable: true }]
            }
          ]
        }],
        policies: { 'session.read': 'ask', 'calendar.read': 'auto' }
      }
    })
    const store = useToolSettingsStore()

    await store.fetchSettings()

    expect(store.groups[0].tools.map((tool) => tool.name)).toEqual(['session_read'])
    expect(store.policies).toEqual({ 'session.read': 'ask' })
  })

  it('loads personal settings through the gate and resets the personal document', async () => {
    const instance = {
      groups: [],
      policies: {},
      webFetch: { enabled: true, defaultPolicy: 'ask', allowedPrivateOrigins: [] }
    }
    api.get.mockImplementation((url) => Promise.resolve({
      data: url === '/tools/personal-settings' ? { enabled: true } : instance
    }))
    api.delete.mockResolvedValue({ data: instance })
    const store = useToolSettingsStore()
    store.selectScope('personal')

    await store.fetchSettings()
    await store.resetPersonalSettings()

    expect(api.get).toHaveBeenCalledWith('/tools/personal-settings')
    expect(api.get).toHaveBeenCalledWith('/tools/settings', { params: { scope: 'personal' } })
    expect(api.delete).toHaveBeenCalledWith('/tools/settings', { params: { scope: 'personal' } })
  })
})
