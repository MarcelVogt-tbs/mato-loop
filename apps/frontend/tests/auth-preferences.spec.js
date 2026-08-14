import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn()
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

  it('loads authentication state without a separate preferences request', async () => {
    api.get.mockResolvedValue({
      data: { setup_required: false, authenticated: true, username: 'viktor' }
    })
    const auth = useAuthStore()

    await auth.fetchStatus()

    expect(auth.authenticated).toBe(true)
    expect(api.get).toHaveBeenCalledOnce()
    expect(api.get).toHaveBeenCalledWith('/auth/status')
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

    await auth.logout()

    expect(chat.attachmentDrafts).toEqual({ newSession: [], sessionMessages: {} })
    expect(chat.audioDictations).toEqual({ newSession: null, sessionMessages: {} })
    expect(identity.displayName).toBe('MatoLoop')
    expect(identity.loaded).toBe(false)
  })
})
