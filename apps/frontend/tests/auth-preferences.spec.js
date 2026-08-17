import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn()
  }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useAuthStore } from '../src/stores/auth'
import { useAgentIdentityStore } from '../src/stores/agent-identity'
import { useChatStore } from '../src/stores/chat'

describe('auth store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  afterEach(() => {
    delete global.window
  })

  it('loads authentication state without a separate preferences request', async () => {
    api.get.mockResolvedValue({
      data: {
        setup_required: false,
        authenticated: true,
        user_id: 'user-1',
        username: 'viktor',
        is_admin: true
      }
    })
    const auth = useAuthStore()

    await auth.fetchStatus()

    expect(auth.authenticated).toBe(true)
    expect(auth.userId).toBe('user-1')
    expect(auth.isAdmin).toBe(true)
    expect(api.get).toHaveBeenCalledOnce()
    expect(api.get).toHaveBeenCalledWith('/auth/status')
  })

  it('does not expose a previous user draft across a cold login', async () => {
    const values = new Map([['matoLoopChatDrafts', JSON.stringify({
      ownerUserId: 'user-1',
      newSession: { message: 'Vertraulich', options: { modelId: 'private-model' } },
      sessionMessages: { private: 'Nur für Benutzer 1' }
    })]])
    global.window = {
      localStorage: {
        getItem: (key) => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, value),
        removeItem: (key) => values.delete(key)
      },
      location: { reload: vi.fn(), replace: vi.fn() }
    }
    api.get.mockResolvedValue({ data: { setup_required: false, authenticated: false } })
    api.post.mockResolvedValue({
      data: { user_id: 'user-2', username: 'neu', is_admin: false }
    })
    const auth = useAuthStore()

    await auth.fetchStatus()
    await auth.login({ username: 'neu', password: 'password' })

    const chat = useChatStore()
    expect(chat.activeDraft).toBe('')
    expect(chat.activeContext.modelId).toBeNull()
    expect(chat.drafts.sessionMessages).toEqual({})
    expect(JSON.parse(values.get('matoLoopChatDrafts')).ownerUserId).toBe('user-2')
  })

  it('clears all in-memory attachment drafts on logout', async () => {
    api.post.mockResolvedValue({ data: {} })
    const auth = useAuthStore()
    const identity = useAgentIdentityStore()
    const chat = useChatStore()
    auth.authenticated = true
    auth.username = 'viktor'
    identity.assignIdentity({ displayName: 'Ada', instructions: 'Direkt' })
    identity.loaded = true
    chat.attachmentDrafts.newSession.push({ id: 'audio', file: { size: 1 } })
    chat.setAudioDictation({ file: { size: 1 }, status: 'failed' })
    chat.setDraft('Nicht für den nächsten Benutzer')

    await auth.logout()

    expect(chat.attachmentDrafts).toEqual({ newSession: [], sessionMessages: {} })
    expect(chat.audioDictations).toEqual({ newSession: null, sessionMessages: {} })
    expect(chat.activeDraft).toBe('')
    expect(identity.displayName).toBe('MatoLoop')
    expect(identity.loaded).toBe(false)
  })

  it('clears persisted drafts and hard reloads when the authenticated user changes', async () => {
    const removeItem = vi.fn()
    const reload = vi.fn()
    global.window = { localStorage: { removeItem }, location: { reload, replace: vi.fn() } }
    api.get.mockResolvedValue({
      data: { authenticated: true, user_id: 'user-2', username: 'neu', is_admin: false }
    })
    const auth = useAuthStore()
    auth.authenticated = true
    auth.userId = 'user-1'
    auth.username = 'alt'

    await auth.fetchStatus()

    expect(auth.userId).toBe('user-2')
    expect(auth.isAdmin).toBe(false)
    expect(removeItem).toHaveBeenCalledWith('matoLoopChatDrafts')
    expect(reload).toHaveBeenCalledOnce()
  })

  it('changes the personal password and reloads the login page', async () => {
    const replace = vi.fn()
    global.window = { localStorage: { removeItem: vi.fn() }, location: { reload: vi.fn(), replace } }
    api.put.mockResolvedValue({ data: null })
    const auth = useAuthStore()

    await auth.changePassword({ current_password: 'altes-passwort', new_password: 'neues-passwort' })

    expect(api.put).toHaveBeenCalledWith('/users/me/password', {
      current_password: 'altes-passwort',
      new_password: 'neues-passwort'
    })
    expect(replace).toHaveBeenCalledWith('/login')
  })
})
