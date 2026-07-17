import { defineRouter } from '#q-app/wrappers'
import { createMemoryHistory, createRouter, createWebHistory } from 'vue-router'

import routes from './routes'

export default defineRouter(() => {
  const createHistory = process.env.SERVER ? createMemoryHistory : createWebHistory

  return createRouter({
    history: createHistory(process.env.VUE_ROUTER_BASE),
    routes,
    scrollBehavior: () => ({ left: 0, top: 0 })
  })
})
