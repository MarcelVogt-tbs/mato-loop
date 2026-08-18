import { defineRouter } from '#q-app/wrappers'
import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'

import routes from './routes'
import { useAuthStore } from 'stores/auth'

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
      if (to.name === 'login' && auth.authenticated) return { name: 'chat' }
      return true
    }
    if (!auth.authenticated) return { name: 'login', query: { redirect: to.fullPath } }
    if (to.meta.requiresAdmin && !auth.isAdmin) return { name: 'settings-providers' }
    return true
  })

  return router
})
