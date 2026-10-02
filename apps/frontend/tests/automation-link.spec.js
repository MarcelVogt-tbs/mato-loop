// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { expect, it, vi } from 'vitest'
import { Quasar, QBanner, QBtn, QSpinner } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('stores/automations', () => ({ useAutomationStore: () => ({ fetchAutomation: async () => ({ id: 'automation', executionSessionId: 'session' }) }) }))
import Link from '../src/features/automations/AutomationLinkPage.vue'

it('opens old automation links in the existing execution session and its app', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/settings/automations/:automationId', component: Link },
    { path: '/', name: 'chat', component: { render: () => null } }
  ] })
  await router.push('/settings/automations/automation?run=run')
  const root = document.createElement('div')
  const app = createApp({ render: () => h(RouterView) })
  app.use(router).use(Quasar, { components: { QBanner, QBtn, QSpinner } })
  app.component('q-page', { render: function () { return h('div', this.$slots.default?.()) } })
  app.mount(root)
  try {
    await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ session: 'session', automation: 'automation', controlCenter: 'automation', run: 'run' }))
  } finally { app.unmount() }
})
