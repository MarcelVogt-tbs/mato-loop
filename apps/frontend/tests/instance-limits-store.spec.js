import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useInstanceLimitsStore } from '../src/stores/instance-limits'

describe('instance limits store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads effective limits for every user and persists admin updates', async () => {
    api.get.mockResolvedValue({ data: { maxUploadMib: 32, maxAttachmentsPerMessage: 4, userQuotaMib: 128 } })
    api.put.mockResolvedValue({ data: { maxUploadMib: 48, maxAttachmentsPerMessage: 6, userQuotaMib: null } })
    const settings = useInstanceLimitsStore()

    await settings.fetchSettings()
    expect(settings.maxUploadBytes).toBe(32 * 1024 * 1024)

    await settings.saveSettings({ maxUploadMib: 48, userQuotaMib: null })
    expect(settings.maxUploadMib).toBe(48)
    expect(settings.maxAttachmentsPerMessage).toBe(6)
    expect(settings.userQuotaMib).toBeNull()
    expect(api.get).toHaveBeenCalledWith('/instance-limits')
    expect(api.put).toHaveBeenCalledWith('/instance-limits', { maxUploadMib: 48, userQuotaMib: null })
  })
})
