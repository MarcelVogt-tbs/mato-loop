import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useAgentIdentityStore } from 'src/stores/agent-identity'
import { useChatStore } from 'src/stores/chat'

function clearAttachmentDraftsAfterIdentityChange(previous, next) {
  if (previous.authenticated && (
    !next.authenticated
    || previous.username !== next.username
  )) {
    useChatStore().clearAllAttachmentDrafts()
    useAgentIdentityStore().resetIdentity()
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    statusLoaded: false,
    setupRequired: false,
    authenticated: false,
    username: null
  }),
  actions: {
    async fetchStatus() {
      const { data } = await api.get('/auth/status')
      const previousIdentity = { authenticated: this.authenticated, username: this.username }
      this.statusLoaded = true
      this.setupRequired = data.setup_required
      this.authenticated = data.authenticated
      this.username = data.username
      clearAttachmentDraftsAfterIdentityChange(previousIdentity, {
        authenticated: this.authenticated,
        username: this.username
      })
      return data
    },
    async setup(credentials) {
      const { data } = await api.post('/auth/setup', credentials)
      const previousIdentity = { authenticated: this.authenticated, username: this.username }
      this.setupRequired = false
      this.authenticated = true
      this.username = data.username
      clearAttachmentDraftsAfterIdentityChange(previousIdentity, {
        authenticated: this.authenticated,
        username: this.username
      })
    },
    async login(credentials) {
      const { data } = await api.post('/auth/login', credentials)
      const previousIdentity = { authenticated: this.authenticated, username: this.username }
      this.authenticated = true
      this.username = data.username
      clearAttachmentDraftsAfterIdentityChange(previousIdentity, {
        authenticated: this.authenticated,
        username: this.username
      })
    },
    async logout() {
      await api.post('/auth/logout')
      useChatStore().clearAllAttachmentDrafts()
      useAgentIdentityStore().resetIdentity()
      this.authenticated = false
      this.username = null
    }
  }
})
