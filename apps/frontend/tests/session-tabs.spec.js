// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar } from 'quasar'
import { quasarOptions } from './quasar-options'
import { useSessionTabsStore } from '../src/features/chat/session-tabs-store'
import { useSessionTabs } from '../src/features/chat/useSessionTabs'
import { useUserPreferencesStore } from '../src/features/settings/user-preferences-store'
import { useChatStore } from '../src/features/chat/store'
import { useProjectStore } from '../src/features/projects/store'
import { useQuestionStore } from '../src/stores/questions'
import SessionTabs from '../src/features/chat/components/SessionTabs.vue'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
import { api } from 'src/boot/axios'

let app, root
const a = { id: 'a', title: 'Alpha', projectId: null }
const b = { id: 'b', title: 'Beta', projectId: 'project' }
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks() })

async function mount() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const chat = useChatStore()
  const tabs = useSessionTabsStore()
  const settings = useUserPreferencesStore()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'chat', component: { render: () => null } }] })
  await router.push({ name: 'chat', query: { session: 'a' } })
  chat.current = a
  let navigation
  const route = reactive({ query: { session: 'a' }, fullPath: '/?session=a' })
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ setup() {
    navigation = useSessionTabs({ chat, route })
    return () => h(SessionTabs, { enabled: navigation.enabled.value, tabs, activeKey: navigation.activeKey.value })
  } })
  app.use(pinia).use(router).use(Quasar, quasarOptions)
  app.mount(root)
  return { chat, tabs, settings, router, route, navigation }
}

it('reuses one preview and keeps edited sessions without losing their drafts when a tab closes', async () => {
  const { chat, tabs, settings, router, route } = await mount()
  expect(root.textContent).toBe('')
  settings.sessionTabsEnabled = true
  await nextTick()
  expect(tabs.items.map(tab => [tab.key, tab.preview])).toEqual([['a', true]])
  chat.current = b
  route.query = { session: 'b', project: 'project' }
  await router.push({ name: 'chat', query: route.query })
  await nextTick()
  expect(tabs.items.map(tab => tab.key)).toEqual(['b'])
  chat.setDraft('Keep this draft')
  expect(tabs.items[0].preview).toBe(false)
  chat.current = a
  route.query = { session: 'a' }
  await router.push({ name: 'chat', query: route.query })
  await nextTick()
  root.querySelector('[aria-label="Tab Beta schließen"]').click()
  await nextTick()
  expect([tabs.items.map(tab => tab.key), chat.drafts.sessionMessages.b]).toEqual([['a'], 'Keep this draft'])
})

it('keeps a preview by double click and avoids duplicates through explicit open', async () => {
  const { tabs, settings, navigation } = await mount()
  settings.sessionTabsEnabled = true
  await nextTick()
  root.querySelector('[role="tab"]').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
  await navigation.openKeptSession(a)
  expect(tabs.items.map(tab => [tab.key, tab.preview])).toEqual([['a', false]])
})

it('retains the active tab when navigation is cancelled', async () => {
  const { tabs, settings, router } = await mount()
  settings.sessionTabsEnabled = true
  await nextTick()
  router.beforeEach(() => false)
  root.querySelector('[aria-label="Tab Alpha schließen"]').click()
  await vi.waitFor(() => expect(tabs.items.map(tab => tab.key)).toEqual(['a']))
})

it('labels project sessions with their full project suffix', async () => {
  const { tabs, settings } = await mount()
  useProjectStore().items = [{ id: 'project', name: 'MatoLoop' }]
  settings.sessionTabsEnabled = true
  tabs.visit(b, { keep: true })
  await nextTick()
  expect([...root.querySelectorAll('[role="tab"]')].map(tab => tab.getAttribute('aria-label')))
    .toEqual(['Beta · MatoLoop', 'Alpha, Vorschau'])
})

it('shows active-run status from summaries instead of session details', async () => {
  const { chat, settings } = await mount()
  chat.current = { ...a, activeRun: { status: 'running' } }
  chat.ensureSessionList().items = [{ ...a, activeRunStatus: 'running' }]
  settings.sessionTabsEnabled = true
  await nextTick()
  expect(root.querySelector('.q-tab--active .q-spinner')).not.toBeNull()
})

it('shows pending questions even before a restored session summary is loaded', async () => {
  const { tabs, settings } = await mount()
  useQuestionStore().pending = [{ id: 'question', sessionId: 'b', status: 'pending' }]
  tabs.visit(b, { keep: true })
  settings.sessionTabsEnabled = true
  await nextTick()
  expect(root.querySelector('[aria-label="1 offene Anfrage"]')).not.toBeNull()
})

it('loads summaries for restored project sessions without loading sidebar scopes', async () => {
  const { tabs } = await mount()
  tabs.$patch({ owner: 'owner', loaded: true, items: [{ key: 'b', sessionId: 'b', title: 'Beta', projectId: 'project', preview: true }] })
  api.get.mockResolvedValueOnce({ data: { items: [{ ...b, activeRunStatus: 'running' }] } })
  await tabs.refreshSummaries()
  expect(tabs.summaries.b.activeRunStatus).toBe('running')
})

it('preserves targeted updates while an in-flight refresh loads other tab summaries', async () => {
  const { tabs } = await mount()
  tabs.$patch({ owner: 'owner', loaded: true, items: [{ key: 'a', sessionId: 'a' }, { key: 'b', sessionId: 'b' }] })
  let resolve
  api.get.mockReturnValueOnce(new Promise(done => { resolve = done }))
  const refreshing = tabs.refreshSummaries()
  const newA = { ...a, title: 'Updated Alpha' }
  tabs.applySummaryUpdates(['a'], { a: newA })
  resolve({ data: { items: [a, b] } })
  await refreshing
  expect(tabs.summaries).toEqual({ a: newA, b })
})

it('removes project draft tabs before saving the remaining tabs', async () => {
  const { tabs, chat } = await mount()
  tabs.visit({ projectId: 'deleted-project' }, { keep: true })
  tabs.visit(a, { keep: true })
  await chat.removeProjectScope('deleted-project')
  expect(tabs.items.map(tab => tab.key)).toEqual(['a'])
})

it('accepts server-pruned tab references after saving', async () => {
  const { tabs } = await mount()
  tabs.$patch({ owner: 'owner', loaded: true })
  api.put.mockResolvedValueOnce({ data: { sessionTabs: [], controlCenterOpen: false } })
  tabs.visit({ id: 'deleted', title: 'Deleted elsewhere' }, { keep: true })
  await tabs.saving
  expect(tabs.items).toEqual([])
})

it('restores the ordered preview list on another device and persists removal', async () => {
  const { tabs } = await mount()
  let saved = { sessionTabs: [
    { sessionId: 'b', projectId: 'project', title: 'Beta', preview: true },
    { sessionId: 'a', projectId: null, title: 'Alpha', preview: false }
  ], controlCenterOpen: true }
  api.get.mockImplementation(async () => ({ data: JSON.parse(JSON.stringify(saved)) }))
  api.put.mockImplementation(async (_url, patch) => ({ data: (saved = { ...saved, ...patch }) }))
  await tabs.setOwner('owner')
  tabs.close('a')
  await tabs.saving
  const otherDevice = useSessionTabsStore(createPinia())
  await otherDevice.setOwner('owner')
  expect([otherDevice.items.map(tab => [tab.key, tab.preview]), otherDevice.controlCenterOpen])
    .toEqual([[['b', true]], true])
})

it('reorders without navigating and keeps the moved preview', async () => {
  const { tabs, settings, router } = await mount()
  settings.sessionTabsEnabled = true
  tabs.visit(b, { keep: true })
  await nextTick()
  const preview = root.querySelector('[aria-label="Alpha, Vorschau"]')
  preview.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', altKey: true, bubbles: true }))
  expect([tabs.items.map(tab => [tab.key, tab.preview]), router.currentRoute.value.query.session])
    .toEqual([[['a', false], ['b', false]], 'a'])
})

it('does not accept a previous account response after the owner changes', async () => {
  const { tabs } = await mount()
  let resolve
  api.get.mockReturnValueOnce(new Promise(done => { resolve = done }))
  const loading = tabs.setOwner('old')
  await tabs.setOwner(null)
  resolve({ data: { sessionTabs: [{ sessionId: 'old-session', preview: false }], controlCenterOpen: true } })
  await loading
  expect([tabs.items, tabs.controlCenterOpen]).toEqual([[], false])
})

it('retains the restored preview when the old route finishes loading later', async () => {
  const { tabs, settings, chat } = await mount()
  chat.loading = true
  settings.sessionTabsEnabled = true
  api.get.mockResolvedValueOnce({ data: { sessionTabs: [
    { sessionId: 'b', projectId: 'project', title: 'Beta', preview: true }
  ], controlCenterOpen: false } })
  api.put.mockResolvedValue({ data: { sessionTabs: [], controlCenterOpen: false } })
  await tabs.setOwner('owner')
  await nextTick()
  chat.loading = false
  await nextTick()
  expect(tabs.items.map(tab => [tab.key, tab.preview])).toEqual([['b', true], ['a', false]])
  await tabs.saving
})

it('waits for restoration before persisting an explicit visibility change', async () => {
  const { tabs } = await mount()
  let resolve
  api.get.mockReturnValueOnce(new Promise(done => { resolve = done }))
  api.put.mockResolvedValue({ data: { sessionTabs: [], controlCenterOpen: true } })
  const loading = tabs.setOwner('owner')
  const opening = tabs.setControlCenterOpen(true)
  resolve({ data: { sessionTabs: [], controlCenterOpen: false } })
  await Promise.all([loading, opening])
  expect(api.put).toHaveBeenLastCalledWith('/users/me/workspace-state', { controlCenterOpen: true })
})
