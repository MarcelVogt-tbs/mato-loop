// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { expect, it, vi } from 'vitest'
import { Quasar, QBanner, QBtn, QSpinner } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('stores/automations', () => ({ useAutomationStore: () => ({
  fetchAutomation: async () => ({ id: 'automation', executionSessionId: 'session' }),
  fetchRun: async () => ({ messageId: 'message' })
}) }))
import Link from '../src/features/automations/AutomationLinkPage.vue'

it.each([false, true])('opens old automation links with run=%s in the existing session', async (withRun) => {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/settings/automations/:automationId', component: Link },
    { path: '/', name: 'chat', component: { render: () => null } }
  ] })
  await router.push(`/settings/automations/automation${withRun ? '?run=run' : ''}`)
  const root = document.createElement('div')
  const app = createApp({ render: () => h(RouterView) })
  app.use(router).use(Quasar, { components: { QBanner, QBtn, QSpinner } })
  app.component('q-page', { render: function () { return h('div', this.$slots.default?.()) } })
  app.mount(root)
  try {
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual(withRun
      ? { session: 'session', message: 'message' }
      : { session: 'session', automation: 'automation', controlCenter: 'automation' }))
  } finally { app.unmount() }
})
