import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { notifyError } from 'src/utils/notifications'
import { applicationServerKey } from 'src/utils/web-push'

const PAGE_SIZE = 20
const DEVICE_ID_KEY = 'matoLoopPushDeviceId'
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function pushSupported() {
  return typeof window !== 'undefined'
    && window.isSecureContext !== false
    && Boolean(window.navigator?.serviceWorker && window.PushManager && window.Notification)
}

function deviceId(create = false) {
  if (typeof window === 'undefined') return null
  try {
    const stored = window.localStorage.getItem(DEVICE_ID_KEY)
    if (UUID_PATTERN.test(stored || '')) return stored
    if (!create) return null
    const id = window.crypto.randomUUID()
    window.localStorage.setItem(DEVICE_ID_KEY, id)
    return id
  } catch (error) {
    if (create) throw error
    return null
  }
}

async function serviceWorkerRegistration(wait = false) {
  if (!pushSupported()) return null
  return wait
    ? window.navigator.serviceWorker.ready
    : window.navigator.serviceWorker.getRegistration('/')
}

function withConfig(config) {
  return Object.keys(config).length ? config : undefined
}

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
    pushSupported: pushSupported(),
    pushLoaded: false,
    pushLoadFailed: false,
    pushServerEnabled: false,
    pushPublicKey: null,
    pushSubscribed: false,
    pushPermission: pushSupported() ? window.Notification.permission : 'unsupported',
    pushLoading: false,
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
    },
    async reconcilePush(config = {}) {
      if (!this.pushSupported || this.pushLoading) return
      this.pushLoading = true
      this.pushLoadFailed = false
      try {
        const { data } = await api.get('/notifications/push/config', withConfig(config))
        this.pushServerEnabled = Boolean(data.enabled)
        this.pushPublicKey = typeof data.publicKey === 'string' ? data.publicKey : null
        this.pushPermission = window.Notification.permission
        const registration = await serviceWorkerRegistration()
        const subscription = await registration?.pushManager.getSubscription()
        this.pushSubscribed = Boolean(subscription)
        if (!subscription) return
        if (!this.pushServerEnabled || this.pushPermission !== 'granted') {
          await subscription.unsubscribe()
          this.pushSubscribed = false
          return
        }
        await api.put(
          `/notifications/push/subscriptions/${deviceId(true)}`,
          subscription.toJSON(),
          withConfig(config)
        )
      } catch (error) {
        this.pushLoadFailed = true
        throw error
      } finally {
        this.pushLoaded = true
        this.pushLoading = false
      }
    },
    async enablePush() {
      if (!this.pushSupported || !this.pushServerEnabled || !this.pushPublicKey || this.pushLoading) return
      this.pushLoading = true
      try {
        this.pushPermission = await window.Notification.requestPermission()
        if (this.pushPermission !== 'granted') return
        const registration = await serviceWorkerRegistration(true)
        let subscription = await registration.pushManager.getSubscription()
        const created = !subscription
        subscription ||= await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: applicationServerKey(this.pushPublicKey)
        })
        try {
          await api.put(
            `/notifications/push/subscriptions/${deviceId(true)}`,
            subscription.toJSON()
          )
        } catch (error) {
          if (created) await subscription.unsubscribe()
          throw error
        }
        this.pushSubscribed = true
      } catch (error) {
        if (!error?.config) {
          notifyError({}, 'Push-Benachrichtigungen konnten in diesem Browser nicht aktiviert werden.')
        }
        throw error
      } finally {
        this.pushLoading = false
      }
    },
    async disablePush() {
      if (!this.pushSupported || this.pushLoading) return
      this.pushLoading = true
      try {
        const id = deviceId()
        if (id) await api.delete(`/notifications/push/subscriptions/${id}`)
        await this.unsubscribePushLocally()
      } finally {
        this.pushLoading = false
      }
    },
    async unsubscribePushLocally() {
      if (!this.pushSupported) return
      try {
        const registration = await serviceWorkerRegistration()
        const subscription = await registration?.pushManager.getSubscription()
        if (subscription) await subscription.unsubscribe()
      } catch {
        // Browser cleanup is best-effort when the session is already invalid.
      }
      this.pushSubscribed = false
    },
    async removePushForLogout() {
      const id = deviceId()
      if (id) {
        try {
          await api.delete(`/notifications/push/subscriptions/${id}`, { suppressErrorToast: true })
        } catch {
          // The authenticated server cleanup is best-effort; local cleanup still follows.
        }
      }
      await this.unsubscribePushLocally()
    }
  }
})
