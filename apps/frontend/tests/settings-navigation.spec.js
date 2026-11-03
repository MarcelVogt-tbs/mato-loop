// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Notify, Quasar, QLayout, QPageContainer } from 'quasar'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('#q-app/wrappers', () => ({ defineRouter: (factory) => factory }))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), patch: vi.fn() } }))
vi.mock('../src/router/routes', async (importOriginal) => {
  const original = await importOriginal()
  const stub = (records) => records.map((record) => ({
    ...record,
    component: { render: () => null },
    ...(record.children ? { children: stub(record.children) } : {})
  }))
  return { ...original, default: stub(original.default) }
})

import createAppRouter from '../src/router/index'
import Page from '../src/features/settings/SettingsPage.vue'
import MemoryPage from '../src/features/memory/MemorySettingsPage.vue'
import { settingsRoutes } from '../src/features/settings/routes'
import { api } from 'src/boot/axios'
import { useAuthStore } from '../src/stores/auth'
import { useProviderStore } from '../src/stores/providers'

let app, root, pinia
beforeEach(() => {
  vi.stubEnv('SERVER', 'true')
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  pinia = createPinia()
  setActivePinia(pinia)
  Object.assign(useAuthStore(), { statusLoaded: true, authenticated: true })
})
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.clearAllMocks()
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
})

it('allows admins personal provider settings regardless of the personal gate', async () => {
  useAuthStore().isAdmin = true
  api.get.mockResolvedValue({ data: { personalProvidersEnabled: false } })
  const router = createAppRouter()
  await router.push('/settings')
  expect(router.currentRoute.value.name).toBe('settings-personalization')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(QLayout, () => h(QPageContainer, () => h(Page))) })
  app.use(pinia).use(router).use(Quasar, quasarOptions)
  app.mount(root)
  const providerLink = () => root.querySelector('a[href="/settings/providers"]')
  expect(providerLink()).not.toBeNull()
  await router.push('/settings/providers')
  expect(router.currentRoute.value.name).toBe('settings-providers')

  api.get.mockResolvedValue({ data: { personalProvidersEnabled: true } })
  await router.push('/settings/providers')
  await nextTick()
  expect(router.currentRoute.value.name).toBe('settings-providers')
  expect(providerLink()).not.toBeNull()

  useProviderStore().personalPolicyLoaded = false
  await nextTick()
  expect(providerLink()).not.toBeNull()
})

it('keeps disabled personal providers out of direct navigation after a policy load failure', async () => {
  const store = useProviderStore()
  store.personalPolicyLoaded = true
  store.personalProvidersEnabled = true
  api.get.mockRejectedValue(new Error('Policy unavailable'))
  const router = createAppRouter()
  await router.push('/settings/providers')
  expect(router.currentRoute.value.name).toBe('settings-personalization')
  expect(store.personalPolicyLoaded).toBe(false)
})

it('keeps Memory tabs visible on run pages and lets the active processing tab return home', async () => {
  api.get.mockResolvedValue({ data: { items: [], total: 0, offset: 0, limit: 20 } })
  const memoryRoute = settingsRoutes.find((route) => route.name === 'settings-memory')
  const router = createRouter({ history: createMemoryHistory(), routes: [{
    ...memoryRoute, path: '/settings/memory', component: MemoryPage,
    children: memoryRoute.children.map((route) => ({ ...route, component: { render: () => null } }))
  }] })
  await router.push('/settings/memory/dream-runs/run-1')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(QLayout, () => h(QPageContainer, () => h(RouterView))) })
  app.use(pinia).use(router).use(Quasar, quasarOptions)
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('.memory-tabs')).not.toBeNull())
  const states = [root.querySelector('.memory-tabs [aria-selected="true"]')?.textContent]
  root.querySelector('.memory-tabs [aria-selected="true"]').click()
  await vi.waitFor(() => expect(router.currentRoute.value.name).toBe('settings-memory'))
  states.push(router.currentRoute.value.query.tab)
  await router.push('/settings/memory/dream-runs')
  await nextTick()
  states.push(root.querySelector('.memory-tabs [aria-selected="true"]')?.textContent)
  const memoriesTab = [...root.querySelectorAll('.memory-tabs [role="tab"]')].find((tab) => tab.textContent.includes('Erinnerungen'))
  memoriesTab.click()
  await vi.waitFor(() => expect(router.currentRoute.value.query.tab).toBe('memories'))
  await router.push('/settings/memory')
  await nextTick()
  states.push(root.querySelector('.memory-tabs [aria-selected="true"]')?.textContent)
  expect(states).toEqual(['syncVerarbeitung', 'dreaming', 'syncVerarbeitung', 'settingsEinstellungen'])
})

it.each(['general', 'dreaming'])('saves %s separately without submitting or discarding the other draft', async (tab) => {
  const context = { automaticMemorySnippetLimit: 2, automaticSessionSnippetLimit: 3, recentSessionSnippetLimit: 2, automaticContextMaxTokens: 1024 }
  const technical = { dreamingInstructions: '', dreamingIntervalMinutes: 60, dreamingIdleMinutes: 15, dreamingMaxBatchMessages: 50, dreamingMaxCandidates: 10, dreamingUpdatePolicy: 'auto', dreamingRemovePolicy: 'ask' }
  const settings = { ...context, ...technical, enabled: true, memoryExtractionEnabled: true }
  api.get.mockImplementation(async (url) => ({ data: url === '/memories/settings' ? settings : { items: [], total: 0, offset: 0, limit: 20 } }))
  api.patch.mockImplementation(async (_url, payload) => ({ data: { ...settings, ...payload } }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/memory', name: 'settings-memory', component: MemoryPage }] })
  await router.push('/settings/memory?tab=dreaming')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(RouterView) })
  app.use(pinia).use(router).use(Quasar, { ...quasarOptions, plugins: { Notify } })
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Memories extrahieren"]').getAttribute('aria-disabled')).toBeNull())
  root.querySelector('textarea').value = 'Unsaved Dreaming instructions'
  root.querySelector('textarea').dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  await router.push('/settings/memory')
  await nextTick()
  const form = root.querySelector('form[aria-label="Automatischer Kontext"]')
  const input = form.querySelector('input[type="number"]')
  input.value = '4'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  await router.push(`/settings/memory?tab=${tab}`)
  await nextTick()
  const saveButton = [...root.querySelectorAll('button')].find((button) => button.textContent.includes('Speichern'))
  saveButton.click()
  await vi.waitFor(() => expect(api.patch).toHaveBeenCalledOnce())
  await router.push(`/settings/memory?tab=${tab === 'general' ? 'dreaming' : 'general'}`)
  await nextTick()
  const draft = tab === 'general' ? root.querySelector('textarea').value : root.querySelector('form input[type="number"]').value
  expect({ request: api.patch.mock.calls[0], draft }).toEqual({
    request: ['/memories/settings', tab === 'general' ? { ...context, automaticMemorySnippetLimit: 4 } : { ...technical, dreamingInstructions: 'Unsaved Dreaming instructions' }],
    draft: tab === 'general' ? 'Unsaved Dreaming instructions' : '4'
  })
})

it.each([
  ['Memories extrahieren', 'memoryExtractionEnabled', false],
  ['Manuell angelegte Erinnerungen einschließen', 'dreamingIncludeManualMemories', true]
])('saves %s independently without discarding the settings draft', async (label, field, value) => {
  const settings = { enabled: true, memoryExtractionEnabled: true, sessionSummariesEnabled: true, dreamingInstructions: '' }
  api.get.mockImplementation(async (url) => ({ data: url === '/memories/settings' ? settings : { items: [], total: 0, offset: 0, limit: 20 } }))
  api.patch.mockImplementation(async (_url, payload) => ({ data: { ...settings, ...payload } }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/memory', name: 'settings-memory', component: MemoryPage }] })
  await router.push('/settings/memory?tab=dreaming')
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(RouterView) })
  app.use(pinia).use(router).use(Quasar, { ...quasarOptions, plugins: { Notify } })
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Session-Zusammenfassungen erstellen"]')?.getAttribute('aria-checked')).toBe('true'))
  root.querySelector('textarea').value = 'Unsaved instructions'
  root.querySelector('textarea').dispatchEvent(new Event('input', { bubbles: true }))
  root.querySelector(`[aria-label="${label}"]`).click()
  await vi.waitFor(() => expect(api.patch).toHaveBeenCalledOnce())
  await nextTick()
  expect({ request: api.patch.mock.calls[0], summaries: root.querySelector('[aria-label="Session-Zusammenfassungen erstellen"]').getAttribute('aria-checked'), draft: root.querySelector('textarea').value }).toEqual({
    request: ['/memories/settings', { [field]: value }], summaries: 'true', draft: 'Unsaved instructions'
  })
})
