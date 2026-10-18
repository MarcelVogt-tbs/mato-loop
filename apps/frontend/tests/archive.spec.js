// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QBanner, QBtn, QExpansionItem, QIcon, QItem, QItemLabel, QItemSection, QLayout, QList, QPage, QPageContainer, QSpinner, Ripple } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
const mocks = vi.hoisted(() => ({ get: vi.fn(), restore: vi.fn(), refresh: vi.fn() }))
vi.mock('src/boot/axios', () => ({ api: { get: mocks.get } }))
vi.mock('src/features/chat/store', () => ({ useChatStore: () => ({ setArchived: mocks.restore }) }))
vi.mock('stores/projects', () => ({ useProjectStore: () => ({}) }))
vi.mock('stores/automations', () => ({ useAutomationStore: () => ({ fetchAutomations: mocks.refresh }) }))
import ArchivePage from '../src/features/archive/ArchivePage.vue'

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.resetAllMocks() })

it('opens archived conversations and restores a session using its archive action', async () => {
  let items = [{ id: 'session', title: 'Archiviertes Gespräch', archivedAt: '2026-10-02T10:00:00Z' }]
  mocks.get.mockImplementation(async (path) => ({ data: { items: path === '/projects' ? [] : items } }))
  mocks.restore.mockImplementation(async () => { items = [] })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'archive', component: ArchivePage },
    { path: '/chat', name: 'chat', component: { render: () => null } }
  ] })
  await router.push('/')
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(QLayout, null, () => h(QPageContainer, null, () => h(RouterView))) })
  app.use(createPinia()).use(router).use(Quasar, { components: { QBanner, QBtn, QExpansionItem, QIcon, QItem, QItemLabel, QItemSection, QList, QPage, QSpinner }, directives: { Ripple } })
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await vi.waitFor(() => expect(root.textContent).toContain('Archiviertes Gespräch'))
  expect(root.querySelector('a').getAttribute('href')).toContain('session=session')
  root.querySelector('[aria-label="Session wiederherstellen"]').click()
  await vi.waitFor(() => expect(root.textContent).toContain('Keine einzeln archivierten Sessions.'))
  expect(mocks.restore).toHaveBeenCalledWith('session', false)
  expect(router.currentRoute.value.name).toBe('archive')
})
