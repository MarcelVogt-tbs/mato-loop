// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar } from 'quasar'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), put: vi.fn() } }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: async () => true }) }))

import ToolPage from '../src/features/settings/ToolSettingsPage.vue'
import IntegrationPage from '../src/features/integrations/IntegrationAccessPage.vue'
import { api } from 'src/boot/axios'
import { useAuthStore } from '../src/stores/auth'
import { useIntegrationStore } from '../src/stores/integrations'
import { useExecutionAgentStore } from '../src/stores/execution-agents'

let app, root
const builtinId = '00000000-0000-4000-8000-000000000001'
const branch = () => ({ policies: {}, integrationPolicies: {}, resourcePolicies: {} })
const rights = () => ({ privatePermissions: branch(), publicPermissions: branch() })
const action = (capability) => ({ capability, label: 'Schreiben', policy: 'ask', effectiveIntegrationPolicy: 'ask', configurable: true, allowedPolicies: ['disabled', 'ask', 'auto'] })
const groups = (capability) => Object.fromEntries(['private', 'public'].map((trust) => [trust, [{ id: 'test', label: 'Rechte', actions: [action(capability)], tools: [] }]]))

async function mount(Page, props, path, name, pinia = createPinia()) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/tools', name: 'settings-tools', component: Page }, { path: '/integrations/:integrationId/resources', name: 'settings-integration-resources', component: Page }, { path: '/integrations', name: 'settings-integrations', component: { render: () => null } }] })
  await router.push(path)
  expect(router.currentRoute.value.name).toBe(name)
  root = document.createElement('div')
  document.body.append(root)
  let page
  app = createApp({ render: () => h(RouterView, null, {
    default: ({ Component }) => h(Component, { ...props, ref: (value) => { page = value?.$?.setupState } })
  }) })
  app.use(pinia).use(router).use(Quasar, quasarOptions)
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  return { page: () => page, pinia }
}
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks(); vi.clearAllMocks() })

it('edits only owned profile tool rights in settings, including for an admin', async () => {
  api.get.mockImplementation(async (url) => ({ data: url === '/agent-profiles' ? {
    personalAgentsEnabled: false, personalToolPermissionsEnabled: false,
    agents: [{ id: builtinId, name: 'Allgemein', scope: 'instance', protected: true }, { id: 'mine', name: 'Mein Agent', scope: 'personal' }],
    effectiveAgents: [],
  } : { ...rights(), groupsByTrust: groups('shell.write'), shellPrefixes: null, inheritedShellPrefixes: { read: [], write: [] } } }))
  api.put.mockResolvedValue({ data: {} })
  const pinia = createPinia()
  useAuthStore(pinia).isAdmin = true
  const { page } = await mount(ToolPage, {}, '/tools', 'settings-tools', pinia)
  await vi.waitFor(() => expect(page().saved).toBeTruthy())
  expect(page().profileOptions.map((item) => item.value)).toEqual(['mine'])
  page().form.permissions.privatePermissions.policies['shell.write'] = 'auto'
  await nextTick()
  root.querySelector('button').click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledWith('/tools/profiles/mine/permissions', expect.objectContaining({ privatePermissions: expect.objectContaining({ policies: { 'shell.write': 'auto' } }) }), { params: { scope: 'personal' } }))
})

it('resets a built-in preset through the administrative tool endpoint', async () => {
  api.get.mockImplementation(async (url) => ({ data: url === '/agent-profiles' ? {
    agents: [{ id: builtinId, name: 'Allgemein', scope: 'instance', protected: true }], effectiveAgents: [],
  } : { ...rights(), groupsByTrust: groups('shell.write'), shellPrefixes: null, inheritedShellPrefixes: { read: [], write: [] } } }))
  api.put.mockResolvedValue({ data: {} })
  const { page } = await mount(ToolPage, { admin: true }, `/tools?profileId=${builtinId}`, 'settings-tools')
  await vi.waitFor(() => expect(page().saved).toBeTruthy())
  const reset = [...root.querySelectorAll('button')].find((button) => button.textContent.includes('auf Standard zurücksetzen'))
  reset.click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledWith(`/tools/profiles/${builtinId}/permissions`, { ...rights(), shellPrefixes: null }, { params: { scope: 'instance' } }))
})

it('edits JavaScript hosts in a cancellable dialog before saving the settings', async () => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  api.get.mockImplementation(async (url) => ({ data: url === '/agent-profiles' ? { agents: [] } : {
    ...rights(), groupsByTrust: { private: [], public: [] }, shellPrefixes: { read: [], write: [] },
    groups: [{ id: 'web', label: 'Web', tools: [{ name: 'webfetch', label: 'Webseite abrufen', icon: 'language' }] }],
    webFetch: { javascriptEnabled: true, javascriptHosts: ['reddit.com', 'www.reddit.com'], userAgent: 'MatoLoop/1.0', maxResponseMiB: 64 },
  } }))
  const { page } = await mount(ToolPage, { admin: true }, '/tools', 'settings-tools')
  await vi.waitFor(() => expect(page().saved).toBeTruthy())
  const button = (label) => [...document.querySelectorAll('button')].find((item) => item.textContent.includes(label))
  const input = (value) => {
    const textarea = document.querySelector('.q-dialog textarea')
    textarea.value = value
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
  }
  expect(root.querySelector('textarea')).toBeNull()
  button('JavaScript-Konfiguration').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog textarea')?.value).toBe('reddit.com\nwww.reddit.com'))
  input('discard.example')
  document.querySelector('.q-dialog [role="switch"]').click()
  await nextTick()
  expect(document.querySelector('.q-dialog textarea')).toBeNull()
  button('Abbrechen').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')).toBeNull())
  expect(page().form.javascriptHostsText).toBe('reddit.com\nwww.reddit.com')
  button('JavaScript-Konfiguration').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog textarea')?.value).toBe('reddit.com\nwww.reddit.com'))
  document.querySelector('.q-dialog [role="switch"]').click()
  await nextTick()
  button('Übernehmen').click()
  await vi.waitFor(() => expect(page().javascriptDialogOpen).toBe(false))
  expect(page().payload().webFetch).toEqual(expect.objectContaining({ javascriptEnabled: false, javascriptHosts: ['reddit.com', 'www.reddit.com'] }))
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')).toBeNull())
  button('JavaScript-Konfiguration').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog [role="switch"]')).not.toBeNull())
  expect(document.querySelector('.q-dialog textarea')).toBeNull()
  document.querySelector('.q-dialog [role="switch"]').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog textarea')?.value).toBe('reddit.com\nwww.reddit.com'))
  input('')
  button('Übernehmen').click()
  await vi.waitFor(() => expect(page().javascriptDialogOpen).toBe(false))
  expect(page().payload().webFetch.javascriptHosts).toEqual([])
  expect(api.put).not.toHaveBeenCalled()
})

it('saves a personal integration override for a global profile without a tool ceiling', async () => {
  const pinia = createPinia()
  const integrations = useIntegrationStore(pinia)
  integrations.catalog = [{ adapterType: 'caldav', availability: 'full', executionLocation: 'backend' }]
  integrations.calendar.accounts = [{ id: 'calendar', name: 'Mein Kalender', providerType: 'caldav' }]
  vi.spyOn(integrations, 'selectScope').mockImplementation(() => { integrations.scope = 'personal' })
  vi.spyOn(integrations, 'fetchCatalog').mockResolvedValue()
  vi.spyOn(useExecutionAgentStore(pinia), 'fetchAll').mockResolvedValue()
  api.get.mockImplementation(async () => ({ data: {
    integrationId: 'calendar', name: 'Mein Kalender', providerType: 'caldav', permissions: { ...rights(), available: null },
    inheritedAvailable: true, profiles: [{ id: builtinId, name: 'Allgemein', scope: 'instance' }],
    inherited: Object.fromEntries(['private', 'public'].map((trust) => [trust, { actions: [action('calendar.write')], resources: [] }]))
  } }))
  vi.spyOn(integrations, 'fetchAccess').mockResolvedValue({})
  api.put.mockResolvedValue({ data: {} })
  const { page } = await mount(IntegrationPage, {}, '/integrations/calendar/resources', 'settings-integration-resources', pinia)
  await vi.waitFor(() => expect(page().permissionsData).toBeTruthy())
  await page().selectProfile(builtinId)
  page().permissions.privatePermissions.policies['calendar.write'] = 'auto'
  await nextTick()
  expect(root.textContent).toContain('Wirksam: Automatisch')
  await page().save()
  expect(api.put).toHaveBeenCalledWith('/integrations/calendar/permissions', expect.objectContaining({ privatePermissions: expect.objectContaining({ policies: { 'calendar.write': 'auto' } }) }), { params: { scope: 'personal', profile_id: builtinId } })
})
