// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QAvatar, QBanner, QBtn, QCard, QCardSection, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QSelect, QSeparator, QTab, QTabs, QToggle, QToolbar, QToolbarTitle } from 'quasar'

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
  app = createApp({ render: () => h(Page, { ...props, ref: (value) => { page = value?.$?.setupState } }) })
  app.use(pinia).use(router).use(Quasar, { components: { QAvatar, QBanner, QBtn, QCard, QCardSection, QExpansionItem, QForm, QIcon, QInnerLoading, QInput, QSelect, QSeparator, QTab, QTabs, QToggle, QToolbar, QToolbarTitle } })
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
