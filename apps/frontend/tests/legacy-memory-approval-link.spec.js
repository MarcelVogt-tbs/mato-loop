// @vitest-environment jsdom
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { Quasar } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))
import { api } from 'src/boot/axios'
import LegacyMemoryApprovalLink from '../src/features/memory/LegacyMemoryApprovalLink.vue'

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.resetAllMocks() })

it('resolves an old approval-only URL into a controlled link to its actual chat', async () => {
  api.get.mockResolvedValue({ data: [
    { id: 'legacy', requestType: 'memory_change', sessionId: 'old-source-chat' }
  ] })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/settings/memory', component: { render: () => null } },
    { path: '/', name: 'chat', component: { render: () => null } }
  ] })
  await router.push('/settings/memory?tab=dreaming&approval=legacy')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp(LegacyMemoryApprovalLink, { approvalId: 'legacy' })
  app.use(createPinia()).use(router).use(Quasar, quasarOptions)
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('a')?.getAttribute('href')).toBe('/?session=old-source-chat'))
})
