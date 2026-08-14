import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'

const PAGE_SIZE = 20

export const useInAppNotificationStore = defineStore('in-app-notifications', {
  state: () => ({
    items: [],
    nextCursor: null,
    unreadCount: 0,
    loading: false,
    loaded: false,
    loadFailed: false,
    loadingMore: false,
    loadMoreFailed: false,
    markingId: null,
    markingAll: false,
    revision: 0
  }),
  actions: {
    async fetchLatest(config = {}) {
      const revision = ++this.revision
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/notifications', {
          ...config,
          params: { ...config.params, pageSize: PAGE_SIZE }
        })
        if (revision !== this.revision) return
        this.items = Array.isArray(data.items) ? data.items : []
        this.nextCursor = data.nextCursor || null
        this.unreadCount = Number(data.unreadCount) || 0
        this.loaded = true
      } catch (error) {
        if (revision === this.revision) this.loadFailed = true
        throw error
      } finally {
        if (revision === this.revision) this.loading = false
      }
    },
    async fetchMore(config = {}) {
      if (!this.nextCursor || this.loadingMore) return
      const cursor = this.nextCursor
      const revision = this.revision
      this.loadingMore = true
      this.loadMoreFailed = false
      try {
        const { data } = await api.get('/notifications', {
          ...config,
          params: { ...config.params, pageSize: PAGE_SIZE, cursor }
        })
        if (revision !== this.revision || cursor !== this.nextCursor) return
        const knownIds = new Set(this.items.map((item) => item.id))
        this.items.push(...(Array.isArray(data.items)
          ? data.items.filter((item) => !knownIds.has(item.id))
          : []))
        this.nextCursor = data.nextCursor || null
        this.unreadCount = Number(data.unreadCount) || 0
      } catch (error) {
        this.loadMoreFailed = true
        throw error
      } finally {
        this.loadingMore = false
      }
    },
    async markRead(id) {
      if (this.markingId || this.markingAll) return
      const current = this.items.find((item) => item.id === id)
      if (current?.readAt) return current
      this.markingId = id
      try {
        const { data } = await api.post(`/notifications/${id}/read`)
        this.revision += 1
        const index = this.items.findIndex((item) => item.id === id)
        if (index !== -1) this.items[index] = data.notification
        this.unreadCount = Number(data.unreadCount) || 0
        return data.notification
      } finally {
        this.markingId = null
      }
    },
    async markAllRead() {
      if (!this.unreadCount || this.markingId || this.markingAll) return
      this.markingAll = true
      try {
        const { data } = await api.post('/notifications/read-all')
        this.revision += 1
        const readAt = new Date().toISOString()
        this.items = this.items.map((item) => item.readAt ? item : { ...item, readAt })
        this.unreadCount = Number(data.unreadCount) || 0
      } finally {
        this.markingAll = false
      }
    }
  }
})
