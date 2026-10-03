import { defineRouter } from '#q-app/wrappers'
import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'

import routes from './routes'
import { useAuthStore } from 'stores/auth'
import { useProviderStore } from 'stores/providers'

export default defineRouter(() => {
  const createHistory = process.env.SERVER ? createMemoryHistory : createWebHistory

  const router = createRouter({
    history: createHistory(process.env.VUE_ROUTER_BASE),
    routes,
    scrollBehavior: () => ({ left: 0, top: 0 })
  })

  router.beforeEach(async (to) => {
    const auth = useAuthStore()
    if (!auth.statusLoaded) await auth.fetchStatus()
    if (to.meta.public) {
      if (to.name === 'login' && auth.authenticated) return { name: 'dashboard' }
      return true
    }
    if (!auth.authenticated) return { name: 'login', query: { redirect: to.fullPath } }
    if (to.meta.requiresAdmin && !auth.isAdmin) return { name: 'settings-personalization' }
    if (to.matched.some((record) => record.name === 'settings')) {
      const providers = useProviderStore()
      if (!providers.personalPolicyLoaded || to.name === 'settings-providers') {
        await providers.fetchPersonalPolicy().catch(() => {})
      }
      if (to.name === 'settings-providers' && (!providers.personalPolicyLoaded || !providers.personalProvidersEnabled)) {
        return { name: 'settings-personalization' }
      }
    }
    return true
  })

  return router
})
