import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn()
  }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useAuthStore } from '../src/stores/auth'

describe('auth preferences', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads the device-independent preference after authentication', async () => {
    api.get.mockImplementation((url) => Promise.resolve({ data: url === '/auth/status'
      ? { setup_required: false, authenticated: true, username: 'viktor' }
      : { thinkingExpanded: true } }))
    const auth = useAuthStore()

    await auth.fetchStatus()

    expect(auth.preferences.thinkingExpanded).toBe(true)
    expect(api.get).toHaveBeenCalledWith('/auth/preferences')
  })

  it('rolls back the expansion state when persistence fails', async () => {
    api.patch.mockRejectedValue(new Error('offline'))
    api.get.mockResolvedValue({ data: { thinkingExpanded: false } })
    const auth = useAuthStore()

    await expect(auth.setThinkingExpanded(true)).rejects.toThrow('offline')

    expect(auth.preferences.thinkingExpanded).toBe(false)
  })
})
