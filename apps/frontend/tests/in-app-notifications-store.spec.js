import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { api } = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn() }
}))

vi.mock('src/boot/axios', () => ({ api }))

import { useInAppNotificationStore } from '../src/stores/in-app-notifications'

describe('in-app notification store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('loads the latest notifications and unread count', async () => {
    api.get.mockResolvedValue({
      data: {
        items: [{ id: 'notification-1', readAt: null }],
        nextCursor: 'next',
        unreadCount: 3
      }
    })
    const store = useInAppNotificationStore()

    await store.fetchLatest({ suppressErrorToast: true })

    expect(api.get).toHaveBeenCalledWith('/notifications', {
      suppressErrorToast: true,
      params: { pageSize: 20 }
    })
    expect(store.items).toEqual([{ id: 'notification-1', readAt: null }])
    expect(store.nextCursor).toBe('next')
    expect(store.unreadCount).toBe(3)
    expect(store.loaded).toBe(true)
  })

  it('does not let an older fetch overwrite newer state', async () => {
    let resolveFirst
    api.get
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce({ data: { items: [{ id: 'new' }], unreadCount: 1 } })
    const store = useInAppNotificationStore()
    const first = store.fetchLatest()

    await store.fetchLatest()
    resolveFirst({ data: { items: [{ id: 'old' }], unreadCount: 9 } })
    await first

    expect(store.items).toEqual([{ id: 'new' }])
    expect(store.unreadCount).toBe(1)
  })

  it('appends pages without duplicate items', async () => {
    api.get.mockResolvedValue({
      data: {
        items: [{ id: 'notification-1' }, { id: 'notification-2' }],
        nextCursor: null,
        unreadCount: 2
      }
    })
    const store = useInAppNotificationStore()
    store.items = [{ id: 'notification-1' }]
    store.nextCursor = 'next'

    await store.fetchMore()

    expect(api.get).toHaveBeenCalledWith('/notifications', {
      params: { pageSize: 20, cursor: 'next' }
    })
    expect(store.items).toEqual([
      { id: 'notification-1' },
      { id: 'notification-2' }
    ])
  })

  it('ignores a page response made stale by a read mutation', async () => {
    let resolvePage
    api.get.mockReturnValue(new Promise((resolve) => { resolvePage = resolve }))
    api.post.mockResolvedValue({
      data: {
        notification: { id: 'notification-1', readAt: '2026-08-14T10:00:00Z' },
        unreadCount: 0
      }
    })
    const store = useInAppNotificationStore()
    store.items = [{ id: 'notification-1', readAt: null }]
    store.nextCursor = 'next'
    store.unreadCount = 1
    const page = store.fetchMore()

    await store.markRead('notification-1')
    resolvePage({
      data: {
        items: [{ id: 'notification-2', readAt: null }],
        nextCursor: null,
        unreadCount: 2
      }
    })
    await page

    expect(store.items).toEqual([
      { id: 'notification-1', readAt: '2026-08-14T10:00:00Z' }
    ])
    expect(store.unreadCount).toBe(0)
  })

  it('marks one notification only after the request succeeds', async () => {
    const updated = { id: 'notification-1', readAt: '2026-08-14T10:00:00Z' }
    api.post.mockResolvedValue({ data: { notification: updated, unreadCount: 1 } })
    const store = useInAppNotificationStore()
    store.items = [{ id: 'notification-1', readAt: null }]
    store.unreadCount = 2

    await store.markRead('notification-1')

    expect(api.post).toHaveBeenCalledWith('/notifications/notification-1/read')
    expect(store.items).toEqual([updated])
    expect(store.unreadCount).toBe(1)
  })

  it('keeps unread state when marking one notification fails', async () => {
    api.post.mockRejectedValue(new Error('request failed'))
    const store = useInAppNotificationStore()
    store.items = [{ id: 'notification-1', readAt: null }]
    store.unreadCount = 1

    await expect(store.markRead('notification-1')).rejects.toThrow('request failed')

    expect(store.items[0].readAt).toBeNull()
    expect(store.unreadCount).toBe(1)
  })

  it('serializes read mutations', async () => {
    let resolveRead
    api.post.mockReturnValue(new Promise((resolve) => { resolveRead = resolve }))
    const store = useInAppNotificationStore()
    store.items = [
      { id: 'notification-1', readAt: null },
      { id: 'notification-2', readAt: null }
    ]
    store.unreadCount = 2
    const first = store.markRead('notification-1')

    await store.markRead('notification-2')
    resolveRead({
      data: {
        notification: { id: 'notification-1', readAt: '2026-08-14T10:00:00Z' },
        unreadCount: 1
      }
    })
    await first

    expect(api.post).toHaveBeenCalledTimes(1)
    expect(store.items[1].readAt).toBeNull()
    expect(store.unreadCount).toBe(1)
  })

  it('marks all loaded notifications after the bulk request succeeds', async () => {
    api.post.mockResolvedValue({ data: { updatedCount: 8, unreadCount: 0 } })
    const store = useInAppNotificationStore()
    store.items = [
      { id: 'notification-1', readAt: null },
      { id: 'notification-2', readAt: '2026-08-13T10:00:00Z' }
    ]
    store.unreadCount = 8

    await store.markAllRead()

    expect(api.post).toHaveBeenCalledWith('/notifications/read-all')
    expect(store.items.every((item) => item.readAt)).toBe(true)
    expect(store.unreadCount).toBe(0)
  })
})
