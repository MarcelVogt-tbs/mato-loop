import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { delete: vi.fn(), get: vi.fn(), patch: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useToolSettingsStore } from '../src/stores/tool-settings'

const settings = (policy = 'ask') => ({
  groups: [{
    id: 'web',
    label: 'Web',
    icon: 'language',
    tools: [{
      name: 'webfetch',
      label: 'Webfetch',
      source: { type: 'builtin' },
      available: false,
      actions: [
        { capability: 'network.fetch.public', configurable: true },
        { capability: 'network.fetch.private', configurable: true }
      ]
    }]
  }],
  policies: {
    'network.fetch.public': policy,
    'network.fetch.private': 'disabled'
  },
  shellPrefixes: { read: [['cat']], write: [] },
  transcribeAudio: { policy: 'ask', timeoutSeconds: 180 }
})

const configuration = {
  groups: [{
    id: 'web',
    label: 'Web',
    icon: 'language',
    tools: [{
      name: 'webfetch',
      label: 'Webfetch',
      icon: 'language',
      available: true,
      resources: [
        { key: 'network.fetch.public', label: 'Öffentliche Ziele', available: true },
        { key: 'network.fetch.private', label: 'Private Ziele', available: false }
      ]
    }]
  }],
  toolAvailability: { webfetch: true },
  resourceAvailability: {
    webfetch: { 'network.fetch.public': true, 'network.fetch.private': false }
  },
  webFetch: { userAgent: 'MatoLoop/1.0', maxResponseMiB: 32 }
}

describe('tool settings store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads and saves only the selected scope and trust branch', async () => {
    api.get.mockResolvedValue({ data: settings() })
    api.put.mockResolvedValue({ data: settings('auto') })
    const store = useToolSettingsStore()
    store.selectScope('personal')
    store.selectTrust('public')
    api.get.mockImplementation((url) => Promise.resolve({
      data: url === '/tools/personal-settings' ? { enabled: true } : settings()
    }))

    await store.fetchSettings()
    await store.saveSettings({ policies: { 'network.fetch.public': 'auto' } })

    expect(api.get).toHaveBeenCalledWith('/tools/settings', {
      params: { scope: 'personal', trust: 'public' }
    })
    expect(api.put).toHaveBeenCalledWith(
      '/tools/settings',
      expect.objectContaining({
        policies: {
          'network.fetch.public': 'auto',
          'network.fetch.private': 'disabled'
        }
      }),
      { params: { scope: 'personal', trust: 'public' } }
    )
    expect(store.policies['network.fetch.public']).toBe('auto')
    expect(store.groups[0].tools[0].available).toBe(false)
  })

  it('discards a late response from the previous trust branch', async () => {
    const requests = []
    api.get.mockImplementation((url, config) => url === '/tools/availability'
      ? Promise.resolve({ data: { tools: {}, resources: {} } })
      : new Promise((resolve) => {
      requests.push({ trust: config.params.trust, resolve })
      }))
    const store = useToolSettingsStore()

    const privateLoad = store.fetchSettings()
    store.selectTrust('public')
    const publicLoad = store.fetchSettings()
    requests[1].resolve({ data: settings('auto') })
    await publicLoad
    requests[0].resolve({ data: settings('disabled') })
    await privateLoad

    expect(store.trust).toBe('public')
    expect(store.policies['network.fetch.public']).toBe('auto')
    expect(store.loading).toBe(false)
  })

  it('does not apply a save after the selected branch changes', async () => {
    let resolveSave
    api.put.mockImplementation(() => new Promise((resolve) => { resolveSave = resolve }))
    const store = useToolSettingsStore()
    store.assignSettings(settings('ask'))
    store.loaded = true

    const save = store.saveSettings({ policies: { 'network.fetch.public': 'auto' } })
    store.selectTrust('public')
    resolveSave({ data: settings('auto') })

    await expect(save).resolves.toBeUndefined()
    expect(store.trust).toBe('public')
    expect(store.loaded).toBe(false)
  })

  it('loads and saves admin-only tool configuration without scope parameters', async () => {
    api.get.mockResolvedValue({ data: configuration })
    api.put.mockResolvedValue({ data: configuration })
    const store = useToolSettingsStore()
    const payload = {
      toolAvailability: { webfetch: true },
      resourceAvailability: {
        webfetch: { 'network.fetch.public': true, 'network.fetch.private': false }
      },
      webFetch: configuration.webFetch
    }

    await store.fetchConfiguration()
    await store.saveConfiguration(payload)

    expect(api.get).toHaveBeenCalledWith('/tools/configuration')
    expect(api.put).toHaveBeenCalledWith('/tools/configuration', payload)
    expect(store.configurationGroups).toEqual(configuration.groups)
    expect(store.configurationWebFetch).toEqual(configuration.webFetch)
  })

  it('updates the loaded policy catalog after availability changes', () => {
    const store = useToolSettingsStore()
    store.assignSettings({
      ...settings(),
      groups: [{
        ...settings().groups[0],
        tools: [{
          ...settings().groups[0].tools[0],
          available: true,
          actions: settings().groups[0].tools[0].actions.map((action) => ({
            ...action,
            available: true
          }))
        }]
      }]
    })

    store.assignConfiguration({
      ...configuration,
      toolAvailability: { webfetch: false },
      resourceAvailability: {
        webfetch: { 'network.fetch.public': false, 'network.fetch.private': false }
      }
    })

    expect(store.groups[0].tools[0].available).toBe(false)
    expect(store.groups[0].tools[0].actions.every((action) => action.available === false)).toBe(true)
    expect(store.transcribeAudio.timeoutSeconds).toBe(180)
  })

  it('discards an older configuration response', async () => {
    const requests = []
    api.get.mockImplementation(() => new Promise((resolve) => requests.push(resolve)))
    const store = useToolSettingsStore()

    const first = store.fetchConfiguration()
    const second = store.fetchConfiguration()
    requests[1]({ data: configuration })
    await second
    requests[0]({ data: { ...configuration, groups: [] } })
    await first

    expect(store.configurationGroups).toEqual(configuration.groups)
  })

  it('keeps the narrow availability endpoint for dictation state', async () => {
    api.get.mockResolvedValue({ data: { tools: { transcribe_audio: true }, resources: {} } })
    const store = useToolSettingsStore()

    await store.fetchAvailability()

    expect(api.get).toHaveBeenCalledWith('/tools/availability')
    expect(store.transcribeAudioAvailable).toBe(true)
  })

  it('reloads the selected trust branch after resetting personal settings', async () => {
    api.delete.mockResolvedValue({ data: {} })
    api.get.mockResolvedValue({ data: settings() })
    const store = useToolSettingsStore()
    store.selectScope('personal')
    store.selectTrust('public')

    await store.resetPersonalSettings()

    expect(api.delete).toHaveBeenCalledWith('/tools/settings', { params: { scope: 'personal' } })
    expect(api.get).toHaveBeenCalledWith('/tools/settings', {
      params: { scope: 'personal', trust: 'public' }
    })
  })
})
