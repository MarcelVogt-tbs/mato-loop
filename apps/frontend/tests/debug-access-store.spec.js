import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }))
vi.mock('../src/boot/axios', () => ({ api }))

import { useDebugAccessStore } from '../src/stores/debug-access'

describe('debug access store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('activates and deactivates without retaining the plaintext token', async () => {
    api.post.mockResolvedValue({ data: { token: 'shown-once', createdAt: '2026-09-17T12:00:00Z' } })
    api.delete.mockResolvedValue({})
    const store = useDebugAccessStore()

    const result = await store.createToken()

    expect(result.token).toBe('shown-once')
    expect(store.$state).not.toHaveProperty('token')
    expect(store.configured).toBe(true)

    await store.revokeToken()
    expect(store.configured).toBe(false)
    expect(store.createdAt).toBeNull()
  })
})
