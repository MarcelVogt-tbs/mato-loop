import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('src/utils/notifications', () => ({ notifyError: vi.fn() }))

import { recoverControlledAuth } from '../src/boot/axios'
import { useAuthStore } from '../src/stores/auth'

function storage() {
  const values = new Map()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key)
  }
}

describe('Axios auth recovery', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    global.window = {
      localStorage: storage(),
      location: { reload: vi.fn(), replace: vi.fn() }
    }
  })

  afterEach(() => {
    vi.restoreAllMocks()
    delete global.window
  })

  it('refreshes identity after an admin denial but ignores invalid login credentials', async () => {
    const auth = useAuthStore()
    auth.authenticated = true
    auth.userId = 'user-1'
    auth.isAdmin = true
    const fetchMe = vi.spyOn(auth, 'fetchMe').mockImplementation((config) => {
      auth.applyIdentity({ userId: 'user-1', username: 'viktor', isAdmin: false }, true)
      return Promise.resolve(config)
    })

    await recoverControlledAuth({
      config: {},
      response: { data: { error: { code: 'auth.invalidCredentials' } } }
    })
    expect(fetchMe).not.toHaveBeenCalled()

    await recoverControlledAuth({
      config: {},
      response: { data: { error: { code: 'auth.adminRequired' } } }
    })

    expect(fetchMe).toHaveBeenCalledWith({
      authRefresh: true,
      suppressErrorToast: true
    })
    expect(auth.isAdmin).toBe(false)
    expect(window.location.reload).toHaveBeenCalledOnce()
  })

  it('invalidates a controlled expired session only once', async () => {
    const auth = useAuthStore()
    auth.authenticated = true
    auth.userId = 'user-1'
    const invalidate = vi.spyOn(auth, 'invalidateSession')
    const error = {
      config: {},
      response: { data: { error: { code: 'auth.sessionExpired' } } }
    }

    await Promise.all([recoverControlledAuth(error), recoverControlledAuth(error)])

    expect(invalidate).toHaveBeenCalledOnce()
    expect(auth.authenticated).toBe(false)
    expect(window.location.replace).toHaveBeenCalledWith('/login')
  })
})
