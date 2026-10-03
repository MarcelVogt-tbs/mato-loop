// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QBanner, QBtn, QCard, QCardSection, QIcon, QItem, QItemLabel, QItemSection, QLayout, QList, QPage, QPageContainer, QSeparator, QSkeleton, Ripple } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
const mocks = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('src/boot/axios', () => ({ api: { get: mocks.get } }))
import DashboardPage from '../src/features/dashboard/DashboardPage.vue'
import { useProjectStore } from '../src/features/projects/store'
import { useAutomationStore } from '../src/features/automations/store'
import { useApprovalStore } from '../src/features/approvals/store'
import { useQuestionStore } from '../src/features/questions/store'
import { useUserProfileStore } from '../src/features/personalization/store'

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.resetAllMocks() })

it('shows effective status, opens a session and lets failed or empty data be refreshed', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const projects = [{ id: 'project-1', name: 'Mein Projekt' }]
  const automations = [{ id: 'automation-1', name: 'Bericht', enabled: true, scheduleCount: 1, effectiveState: { enabled: false, nextRunAt: null } }]
  useProjectStore().$patch({ items: projects, loaded: true })
  useAutomationStore().$patch({ items: automations, loaded: true })
  useApprovalStore().$patch({ pending: [{ id: 'approval-1' }], loaded: true })
  useQuestionStore().$patch({ loaded: true })
  let sessions = [{ id: 'session-1', title: 'Ein Gespräch fortsetzen', projectId: 'project-1', createdAt: '2026-10-03T12:00:00Z', hasUnreadMessages: true, activeRunStatus: 'running' }]
  let failed = false
  mocks.get.mockImplementation(async (path) => {
    if (path === '/sessions' && failed) throw new Error('unavailable')
    return { data: {
      '/sessions': { items: sessions }, '/projects': { items: projects },
      '/automations': { items: automations }, '/execution-agents': { items: [{ id: 'computer-1', connected: true }, { id: 'computer-2', connected: false }] },
      '/approvals/pending': [{ id: 'approval-1' }], '/questions/pending': [],
      '/user-profile': { preferredName: { privateOnly: 'Ada', publicOnly: 'A.' }, fullName: { both: 'Ada Lovelace' } }
    }[path] }
  })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/dashboard', name: 'dashboard', component: DashboardPage },
    ...['chat', 'settings-execution-agents', 'settings-automation-create', 'settings-automation-detail'].map((name) => ({ path: `/${name}${name === 'settings-automation-detail' ? '/:automationId' : ''}`, name, component: { render: () => null } }))
  ] })
  await router.push('/dashboard')
  root = document.createElement('div'); document.body.append(root)
  app = createApp({ render: () => h(QLayout, null, () => h(QPageContainer, null, () => h(RouterView))) })
  app.use(pinia).use(router).use(Quasar, { components: { QBanner, QBtn, QCard, QCardSection, QIcon, QItem, QItemLabel, QItemSection, QList, QPage, QSeparator, QSkeleton }, directives: { Ripple } })
  app.mount(root)

  await vi.waitFor(() => expect(root.textContent).toContain('Ein Gespräch fortsetzen'))
  expect(root.querySelector('h1').textContent).toBe('Willkommen zurück, Ada')
  useUserProfileStore().assignProfile({ fullName: { both: 'Ada Lovelace' } })
  await vi.waitFor(() => expect(root.querySelector('h1').textContent).toBe('Willkommen zurück, Ada Lovelace'))
  useUserProfileStore().assignProfile({})
  await vi.waitFor(() => expect(root.querySelector('h1').textContent).toBe('Willkommen zurück'))
  expect(mocks.get).toHaveBeenCalledWith('/sessions', { params: { pageSize: 6, sort: 'recent' } })
  expect(root.textContent).toContain('Neue Antwort')
  expect(root.textContent).toContain('Agent arbeitet')
  expect(root.textContent).toContain('1 von 2 Computern online')
  expect(root.textContent).toContain('1 offene Anfrage')
  expect(root.querySelector('#dashboard-automations')).toBeNull()
  useAutomationStore().items[0].effectiveState = { enabled: true, nextRunAt: '2026-10-04T12:00:00Z' }
  await vi.waitFor(() => expect(root.textContent).toContain('Nächste Automationen'))
  root.querySelector('a[href*="session=session-1"]').click()
  await vi.waitFor(() => expect(router.currentRoute.value.query).toEqual({ session: 'session-1', project: 'project-1' }))
  await router.push('/dashboard')
  await vi.waitFor(() => expect(root.textContent).toContain('Ein Gespräch fortsetzen'))
  failed = true
  root.querySelector('[aria-label="Dashboard aktualisieren"]').click()
  await vi.waitFor(() => expect(root.textContent).toContain('Sessions konnten nicht geladen werden.'))
  failed = false
  sessions = []
  root.querySelector('[aria-label="Dashboard aktualisieren"]').click()
  await vi.waitFor(() => expect(root.textContent).toContain('Erste Session starten'))
})
