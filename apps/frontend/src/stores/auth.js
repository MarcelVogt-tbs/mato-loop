import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useAgentIdentityStore } from 'src/stores/agent-identity'
import { useChatStore } from 'src/stores/chat'

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
    clearIdentityState() {
      const chat = useChatStore()
      chat.clearAllDrafts()
      chat.setDraftOwner(null)
      useAgentIdentityStore().resetIdentity()
    },
    applyIdentity(data, authenticated = data.authenticated) {
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
      if (accountChanged) this.clearIdentityState()
      Object.assign(this, next)
      useChatStore().setDraftOwner(next.authenticated ? next.userId : null)
      if (accountChanged) {
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
      this.applyIdentity(data)
      return data
    },
    async fetchMe(config = null) {
      const { data } = await api.get('/auth/me', config || undefined)
      this.applyIdentity(data, true)
      return data
    },
    invalidateSession() {
      this.clearIdentityState()
      this.authenticated = false
      this.userId = null
      this.username = null
      this.isAdmin = false
      hardReload('/login')
    },
    async setup(credentials) {
      const { data } = await api.post('/auth/setup', credentials)
      this.setupRequired = false
      this.applyIdentity(data, true)
    },
    async login(credentials) {
      const { data } = await api.post('/auth/login', credentials)
      this.applyIdentity(data, true)
    },
    async changePassword(payload) {
      await api.put('/users/me/password', payload)
      this.clearIdentityState()
      hardReload('/login')
    },
    async logout() {
      await api.post('/auth/logout')
      this.clearIdentityState()
      this.authenticated = false
      this.userId = null
      this.username = null
      this.isAdmin = false
      hardReload('/login')
    }
  }
})
