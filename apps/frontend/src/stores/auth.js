import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useChatStore } from 'src/stores/chat'

function clearAttachmentDraftsAfterIdentityChange(previous, next) {
  if (previous.authenticated && (
    !next.authenticated
    || previous.username !== next.username
  )) {
    useChatStore().clearAllAttachmentDrafts()
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    statusLoaded: false,
    setupRequired: false,
    authenticated: false,
    username: null,
    preferences: { thinkingExpanded: false },
    confirmedThinkingExpanded: false,
    preferenceRevision: 0
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
      if (data.authenticated) await this.fetchPreferences()
      return data
    },
    async fetchPreferences() {
      this.preferences = (await api.get('/auth/preferences')).data
      this.confirmedThinkingExpanded = this.preferences.thinkingExpanded
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
      await this.fetchPreferences()
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
      await this.fetchPreferences()
    },
    async logout() {
      await api.post('/auth/logout')
      useChatStore().clearAllAttachmentDrafts()
      this.authenticated = false
      this.username = null
      this.preferences = { thinkingExpanded: false }
      this.confirmedThinkingExpanded = false
    },
    async setThinkingExpanded(expanded) {
      const revision = ++this.preferenceRevision
      this.preferences.thinkingExpanded = expanded
      try {
        const { data } = await api.patch('/auth/preferences', {
          thinking_expanded: expanded
        })
        if (revision === this.preferenceRevision) {
          this.preferences = data
          this.confirmedThinkingExpanded = data.thinkingExpanded
        }
      } catch (error) {
        if (revision === this.preferenceRevision) {
          try {
            await this.fetchPreferences()
          } catch {
            this.preferences.thinkingExpanded = this.confirmedThinkingExpanded
          }
        }
        throw error
      }
    }
  }
})
