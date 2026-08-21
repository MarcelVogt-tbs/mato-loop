import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useAgentIdentityStore } from 'src/stores/agent-identity'
import { useChatStore } from 'src/stores/chat'
import { notifyError } from 'src/utils/notifications'

function identityFrom(data, authenticated = data.authenticated) {
  return {
    authenticated: Boolean(authenticated),
    userId: data.userId ?? data.user_id ?? null,
    username: data.username ?? null,
    isAdmin: Boolean(data.isAdmin ?? data.is_admin)
  }
}

function hardReload(path) {
  if (typeof window === 'undefined') return
  if (path) window.location.replace(path)
  else window.location.reload()
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    statusLoaded: false,
    setupRequired: false,
    authenticated: false,
    userId: null,
    username: null,
    isAdmin: false
  }),
  actions: {
    async clearIdentityState() {
      const chat = useChatStore()
      try {
        await chat.clearAllDrafts()
        return true
      } catch {
        notifyError({}, 'Vorgemerkte Nachrichten konnten nicht sicher gelöscht werden.')
        return false
      } finally {
        chat.setDraftOwner(null)
        useAgentIdentityStore().resetIdentity()
      }
    },
    async applyIdentity(data, authenticated = data.authenticated) {
      const previous = {
        authenticated: this.authenticated,
        userId: this.userId,
        username: this.username,
        isAdmin: this.isAdmin
      }
      const next = identityFrom(data, authenticated)
      const accountChanged = previous.authenticated && (
        !next.authenticated || previous.userId !== next.userId
      )
      const clearing = accountChanged ? this.clearIdentityState() : null
      Object.assign(this, next)
      useChatStore().setDraftOwner(next.authenticated ? next.userId : null)
      if (accountChanged) {
        await clearing
        hardReload(next.authenticated ? null : '/login')
      } else if (previous.authenticated && previous.isAdmin !== next.isAdmin) {
        hardReload()
      }
      return next
    },
    async fetchStatus() {
      const { data } = await api.get('/auth/status')
      this.statusLoaded = true
      this.setupRequired = data.setupRequired ?? data.setup_required
      await this.applyIdentity(data)
      return data
    },
    async fetchMe(config = null) {
      const { data } = await api.get('/auth/me', config || undefined)
      await this.applyIdentity(data, true)
      return data
    },
    async invalidateSession() {
      const clearing = this.clearIdentityState()
      this.authenticated = false
      this.userId = null
      this.username = null
      this.isAdmin = false
      await clearing
      hardReload('/login')
    },
    async setup(credentials) {
      const { data } = await api.post('/auth/setup', credentials)
      this.setupRequired = false
      await this.applyIdentity(data, true)
    },
    async login(credentials) {
      const { data } = await api.post('/auth/login', credentials)
      await this.applyIdentity(data, true)
    },
    async changePassword(payload) {
      await api.put('/users/me/password', payload)
      await this.clearIdentityState()
      hardReload('/login')
    },
    async logout() {
      await api.post('/auth/logout')
      await this.clearIdentityState()
      this.authenticated = false
      this.userId = null
      this.username = null
      this.isAdmin = false
      hardReload('/login')
    }
  }
})
