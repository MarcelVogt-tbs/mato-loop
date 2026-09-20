import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { put: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))
vi.mock('src/utils/notifications', () => ({ notifyError: vi.fn() }))

import { useChatStore } from '../src/stores/chat'

describe('chat presence', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('marks the visible session without showing transient errors', async () => {
    api.put.mockResolvedValue({ data: null })
    const chat = useChatStore()

    await chat.markSessionPresent('session-1')

    expect(api.put).toHaveBeenCalledWith('/sessions/session-1/presence', null, {
      suppressErrorToast: true
    })
  })
})
