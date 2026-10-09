import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { notifyError } from 'src/utils/notifications'

export async function applyTheme(url) {
  const previous = document.getElementById('appearance-theme')
  if (previous?.getAttribute('href') === url) {
    document.head.append(previous)
    return
  }
  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = url
  link.media = 'not all'
  await new Promise((resolve, reject) => {
    const timer = setTimeout(failed, 10000)
    function failed() {
      clearTimeout(timer)
      link.remove()
      reject({ error: { code: 'appearance.themeLoadFailed' } })
    }
    link.onload = () => { clearTimeout(timer); resolve() }
    link.onerror = failed
    document.head.append(link)
  })
  previous?.remove()
  link.id = 'appearance-theme'
  link.media = 'all'
}

export function applyBranding(appearance, unreadCount = 0) {
  document.title = unreadCount > 0 ? `(${unreadCount > 99 ? '99+' : unreadCount}) ${appearance.appName}` : appearance.appName
  for (const [name, content] of [['description', appearance.description], ['apple-mobile-web-app-title', appearance.appName]]) {
    document.querySelector(`meta[name="${name}"]`)?.setAttribute('content', content)
  }
  for (const icon of document.querySelectorAll('link[rel="icon"]')) {
    icon.href = unreadCount > 0 && !appearance.customLogo ? '/icons/matoloop-unread.svg' : appearance.logoUrl
    icon.type = appearance.customLogo ? 'image/png' : 'image/svg+xml'
    icon.sizes = 'any'
  }
  const apple = document.querySelector('link[rel="apple-touch-icon"]')
  if (apple) apple.href = appearance.customLogo ? appearance.logoUrl : '/apple-touch-icon.png'
}

export const useAppearanceStore = defineStore('appearance', {
  state: () => ({
    appName: 'MatoLoop', description: 'Deine selbst gehostete AI-Chat- und Agent-Harness',
    logoUrl: '/icons/matoloop.svg', customLogo: false,
    defaultThemeId: 'matoloop', effectiveThemeId: 'matoloop', appliedThemeId: 'matoloop',
    stylesheetUrl: '/themes/matoloop/theme.css', themes: [],
    loaded: false, loading: false, loadFailed: false, saving: false
  }),
  actions: {
    async assignAppearance(data) {
      Object.assign(this, data)
      try {
        await applyTheme(this.stylesheetUrl)
        this.appliedThemeId = this.effectiveThemeId
      } catch (error) { notifyError(error) }
    },
    async fetchAppearance() {
      this.loading = true
      this.loadFailed = false
      try {
        const { data } = await api.get('/appearance', { timeout: 5000 })
        await this.assignAppearance(data)
        this.loaded = true
      } catch (error) {
        this.loadFailed = true
        throw error
      } finally { this.loading = false }
    },
    async saveBranding(payload) {
      return this.save(() => api.put('/appearance/branding', payload))
    },
    async saveThemes(payload) {
      return this.save(() => api.put('/appearance/themes', payload))
    },
    async uploadLogo(file) {
      const data = new FormData()
      data.append('file', file)
      return this.save(() => api.put('/appearance/logo', data))
    },
    async resetLogo() {
      return this.save(() => api.delete('/appearance/logo'))
    },
    async save(request) {
      this.saving = true
      try {
        const { data } = await request()
        await this.assignAppearance(data)
      } finally { this.saving = false }
    }
  }
})
