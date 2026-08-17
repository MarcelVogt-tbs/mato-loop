import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useFileMediaSettingsStore } from '../src/stores/file-media-settings'

describe('file media settings store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads effective limits for every user and persists admin updates', async () => {
    api.get.mockResolvedValue({ data: { maxUploadMib: 32, maxAttachmentsPerMessage: 4 } })
    api.put.mockResolvedValue({ data: { maxUploadMib: 48, maxAttachmentsPerMessage: 6 } })
    const settings = useFileMediaSettingsStore()

    await settings.fetchSettings()
    expect(settings.maxUploadBytes).toBe(32 * 1024 * 1024)

    await settings.saveSettings({ maxUploadMib: 48 })
    expect(settings.maxUploadMib).toBe(48)
    expect(settings.maxAttachmentsPerMessage).toBe(6)
    expect(api.get).toHaveBeenCalledWith('/file-media-settings')
    expect(api.put).toHaveBeenCalledWith('/file-media-settings', { maxUploadMib: 48 })
  })
})
