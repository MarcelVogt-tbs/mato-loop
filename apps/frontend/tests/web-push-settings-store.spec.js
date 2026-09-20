import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useWebPushSettingsStore } from '../src/stores/web-push-settings'

describe('web push settings store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads and saves the server configuration', async () => {
    api.get.mockResolvedValue({
      data: { enabled: false, configured: false, contactEmail: null }
    })
    api.put.mockResolvedValue({
      data: { enabled: true, configured: true, contactEmail: 'admin@example.test' }
    })
    const store = useWebPushSettingsStore()

    await store.fetchSettings()
    await store.saveSettings({ enabled: true, contactEmail: 'admin@example.test' })

    expect(api.get).toHaveBeenCalledWith('/notifications/push/settings')
    expect(api.put).toHaveBeenCalledWith('/notifications/push/settings', {
      enabled: true,
      contactEmail: 'admin@example.test'
    })
    expect(store.$state).toMatchObject({
      enabled: true,
      configured: true,
      contactEmail: 'admin@example.test',
      loaded: true
    })
  })
})
