// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  Quasar, QIcon, QItem, QItemSection, QLayout, QList, QPage, QPageContainer,
  QRouteTab, QSeparator, QTabs
} from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('#q-app/wrappers', () => ({ defineRouter: (factory) => factory }))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))
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
  app.use(pinia).use(router).use(Quasar, { components: {
    QIcon, QItem, QItemSection, QList, QPage, QRouteTab, QSeparator, QTabs
  } })
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
