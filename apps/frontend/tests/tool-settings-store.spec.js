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

  it('loads and saves the WebFetch contract without changing field names', async () => {
    const response = {
      webFetch: {
        enabled: true,
        defaultPolicy: 'auto_allowed',
        allowedPrivateOrigins: ['http://127.0.0.1:8080']
      }
    }
    api.get.mockResolvedValue({ data: response })
    api.put.mockResolvedValue({ data: response })
    const store = useToolSettingsStore()

    await store.fetchSettings()
    await store.saveSettings(response.webFetch)

    expect(api.get).toHaveBeenCalledWith('/tools/settings')
    expect(api.put).toHaveBeenCalledWith('/tools/settings', response)
    expect(store.webFetch).toEqual(response.webFetch)
    expect(store.loaded).toBe(true)
  })

  it('keeps saving disabled after a failed initial load', async () => {
    api.get.mockRejectedValue(new Error('offline'))
    const store = useToolSettingsStore()

    await expect(store.fetchSettings()).rejects.toThrow('offline')

    expect(store.loaded).toBe(false)
    expect(store.loadFailed).toBe(true)
  })
})
