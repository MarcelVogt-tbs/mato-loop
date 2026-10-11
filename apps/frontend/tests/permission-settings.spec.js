// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar } from 'quasar'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), put: vi.fn() } }))
const { confirm } = vi.hoisted(() => ({ confirm: vi.fn(async () => true) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))

import ToolPage from '../src/features/settings/ToolSettingsPage.vue'
import IntegrationPage from '../src/features/integrations/IntegrationAccessPage.vue'
import IntegrationPermissionEditor from '../src/features/integrations/IntegrationPermissionEditor.vue'
import GeneralPage from '../src/features/settings/GeneralSettingsPage.vue'
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

it('saves the model distinction switch from basic settings', async () => {
  const current = { publicBaseUrl: null, effectivePublicBaseUrl: 'https://mato.test', publicUrlSource: 'automatic', modelTrustEnabled: true }
  api.get.mockResolvedValue({ data: current })
  api.put.mockResolvedValue({ data: { ...current, modelTrustEnabled: false } })
  await mount(GeneralPage, {}, '/tools', 'settings-tools')
  await vi.waitUntil(() => root.querySelector('.q-toggle:not(.disabled)'))
  root.querySelector('.q-toggle').click()
  await nextTick()
  root.querySelector('button').click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledWith('/general-settings', { publicBaseUrl: null, modelTrustEnabled: false }))
})

it.each([true, false])('confirms visible tool presets and does not reapply them on ordinary saves (model distinction: %s)', async (modelTrustEnabled) => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const current = { publicBaseUrl: null, effectivePublicBaseUrl: 'https://mato.test', publicUrlSource: 'automatic', modelTrustEnabled, toolPermissionPresets: { private: null, public: 'ask_all' } }
  const chosen = { private: 'ask_delete_sensitive', ...(modelTrustEnabled ? { public: 'ask_sensitive' } : {}) }
  api.get.mockResolvedValue({ data: current })
  api.put.mockResolvedValue({ data: { ...current, toolPermissionPresets: { ...current.toolPermissionPresets, ...chosen } } })
  await mount(GeneralPage, {}, '/tools', 'settings-tools')
  const choose = async (label, option) => {
    const select = await vi.waitUntil(() => root.querySelector(`[aria-label="${label}: Tool-Vorgabe"]:not([aria-disabled="true"])`))
    select.click()
    const item = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find(item => item.querySelector('.q-item__label')?.textContent === option))
    item.click()
    await vi.waitUntil(() => !document.querySelector('[role="option"]'))
  }
  await vi.waitFor(() => expect(root.textContent).toContain('Individuell angepasst'))
  expect(root.querySelector('a[aria-label="Tool-Vorgaben erklärt (öffnet neuen Tab)"]').getAttribute('href')).toBe('/docs/tools-und-integrationen/#globale-tool-vorgaben-anwenden')
  await choose(modelTrustEnabled ? 'Privat' : 'Alle Modelle', 'Löschen und sensible Zugriffe bestätigen')
  if (modelTrustEnabled) await choose('Öffentlich', 'Nur sensible Zugriffe bestätigen')
  confirm.mockResolvedValueOnce(false)
  root.querySelector('button').click()
  await vi.waitFor(() => expect(confirm).toHaveBeenCalledOnce())
  expect(api.put).not.toHaveBeenCalled()
  await nextTick()
  root.querySelector('button').click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledWith('/general-settings', { publicBaseUrl: null, modelTrustEnabled, toolPermissionPresets: chosen }))
  await vi.waitUntil(() => !root.querySelector('button').disabled)
  root.querySelector('button').click()
  await vi.waitFor(() => expect(api.put).toHaveBeenLastCalledWith('/general-settings', { publicBaseUrl: null, modelTrustEnabled }))
})

it('edits only owned profile tool rights in settings, including for an admin', async () => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  api.get.mockImplementation(async (url) => ({ data: url === '/agent-profiles' ? {
    personalAgentsEnabled: false, personalToolPermissionsEnabled: false,
    agents: [{ id: builtinId, name: 'Allgemein', scope: 'instance', protected: true }, { id: 'mine', name: 'Mein Agent', scope: 'personal' }],
    effectiveAgents: [],
  } : { ...rights(), publicPermissions: { ...branch(), policies: { 'shell.write': 'disabled' } }, groupsByTrust: groups('shell.write'), shellPrefixes: null, inheritedShellPrefixes: { read: [], write: [] } } }))
  api.put.mockResolvedValue({ data: {} })
  const pinia = createPinia()
  useAuthStore(pinia).isAdmin = true
  useAuthStore(pinia).modelTrustEnabled = false
  const { page } = await mount(ToolPage, {}, '/tools', 'settings-tools', pinia)
  await vi.waitFor(() => expect(page().saved).toBeTruthy())
  expect(page().profileOptions.map((item) => item.value)).toEqual(['mine'])
  expect(root.textContent).not.toContain('Öffentliche Modelle')
  const button = (label) => [...document.querySelectorAll('button')].find((item) => item.textContent.includes(label))
  const select = async (label, option) => {
    const field = [...document.querySelectorAll('.q-dialog .q-field')].find((item) => item.querySelector('.q-field__label')?.textContent === label)
    const control = field.querySelector('[role="combobox"]')
    control.focus()
    control.click()
    const choice = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find((item) => item.querySelector('.q-item__label')?.textContent === option))
    choice.click()
    await vi.waitUntil(() => !document.querySelector('[role="option"]'))
  }
  button('Override hinzufügen').click()
  await vi.waitUntil(() => document.querySelector('.q-dialog'))
  await select('Aktion', 'Schreiben')
  await select('Alle Modelle', 'Automatisch')
  button('Übernehmen').click()
  await vi.waitUntil(() => !document.querySelector('.q-dialog'))
  root.querySelector('button').click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledWith('/tools/profiles/mine/permissions', expect.objectContaining({
    privatePermissions: expect.objectContaining({ policies: { 'shell.write': 'auto' } }),
    publicPermissions: expect.objectContaining({ policies: { 'shell.write': 'disabled' } })
  }), { params: { scope: 'personal' } }))
})

it('resets a built-in preset through the administrative tool endpoint', async () => {
  api.get.mockImplementation(async (url) => ({ data: url === '/agent-profiles' ? {
    agents: [{ id: builtinId, name: 'Allgemein', scope: 'instance', protected: true }], effectiveAgents: [],
  } : { ...rights(), groupsByTrust: groups('shell.write'), shellPrefixes: null, inheritedShellPrefixes: { read: [], write: [] } } }))
  api.put.mockResolvedValue({ data: {} })
  const { page } = await mount(ToolPage, { admin: true }, `/tools?profileId=${builtinId}`, 'settings-tools')
  await vi.waitFor(() => expect(page().saved).toBeTruthy())
  const reset = root.querySelector('[aria-label="Alle Overrides löschen"]')
  expect(reset.disabled).toBe(true)
  page().form.permissions.privatePermissions.policies['shell.write'] = 'auto'
  await nextTick()
  reset.click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledWith(`/tools/profiles/${builtinId}/permissions`, { ...rights(), shellPrefixes: null }, { params: { scope: 'instance' } }))
})

it('edits JavaScript hosts in a cancellable dialog before saving the settings', async () => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const catalog = [{ id: 'web', label: 'Web', tools: [{ name: 'webfetch', label: 'Webseite abrufen', icon: 'language', actions: [action('network.fetch')] }] }]
  api.get.mockImplementation(async (url) => ({ data: url === '/agent-profiles' ? { agents: [] } : {
    ...rights(), privateGroups: catalog, publicGroups: catalog, shellPrefixes: { read: [], write: [] },
    groups: catalog,
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
  expect(root.textContent).not.toContain('JavaScript-Konfiguration')
  await page().selectSection('management')
  await nextTick()
  button('JavaScript-Konfiguration').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog textarea')?.value).toBe('reddit.com\nwww.reddit.com'))
  input('discard.example')
  button('Abbrechen').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')).toBeNull())
  expect(page().form.javascriptHostsText).toBe('reddit.com\nwww.reddit.com')
  button('JavaScript-Konfiguration').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog textarea')?.value).toBe('reddit.com\nwww.reddit.com'))
  document.querySelector('.q-dialog [role="switch"]').click()
  await nextTick()
  button('Übernehmen').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')).toBeNull())
  expect(page().payload().webFetch).toEqual(expect.objectContaining({ javascriptEnabled: false, javascriptHosts: ['reddit.com', 'www.reddit.com'] }))
  expect(api.put).not.toHaveBeenCalled()
})

it.each(['Abbrechen', 'Übernehmen'])('handles shell prefix drafts with %s', async (decision) => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  api.get.mockImplementation(async (url) => ({ data: url === '/agent-profiles' ? {
    agents: [{ id: builtinId, name: 'Allgemein', scope: 'instance', protected: true }], effectiveAgents: []
  } : { ...rights(), groupsByTrust: groups('shell.read'), shellPrefixes: { read: [['git', 'status']], write: [] } } }))
  const { page } = await mount(ToolPage, { admin: true }, `/tools?profileId=${builtinId}`, 'settings-tools')
  await vi.waitUntil(() => root.querySelector('button:not([disabled])') && page().saved)
  const button = (label) => [...document.querySelectorAll('button')].find((item) => item.textContent.includes(label))
  root.querySelector('[aria-label="Shell-Präfixe bearbeiten"]').click()
  const textarea = await vi.waitUntil(() => document.querySelector('.q-dialog textarea'))
  textarea.value = 'git diff'
  textarea.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
  button(decision).click()
  await vi.waitUntil(() => !document.querySelector('.q-dialog'))
  expect(page().payload().shellPrefixes).toEqual({ read: [['git', decision === 'Übernehmen' ? 'diff' : 'status']], write: [] })
})

it('adds an explicitly empty shell setting override and removes it back to inheritance', async () => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  api.get.mockImplementation(async (url) => ({ data: url === '/agent-profiles' ? {
    agents: [{ id: builtinId, name: 'Allgemein', scope: 'instance', protected: true }], effectiveAgents: []
  } : { ...rights(), groupsByTrust: groups('shell.read'), shellPrefixes: null, inheritedShellPrefixes: { read: [], write: [] } } }))
  const { page } = await mount(ToolPage, { admin: true }, `/tools?profileId=${builtinId}`, 'settings-tools')
  await vi.waitUntil(() => page().saved)
  const button = (label) => [...document.querySelectorAll('button')].find((item) => item.textContent.includes(label))
  expect(root.querySelector('[aria-label="Shell-Präfixe bearbeiten"]')).toBeNull()
  button('Override hinzufügen').click()
  const select = await vi.waitUntil(() => document.querySelector('.q-dialog [role="combobox"]'))
  select.focus()
  select.click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent.includes('Einstellung')))
  option.click()
  await vi.waitUntil(() => document.querySelector('.q-dialog textarea'))
  button('Übernehmen').click()
  await vi.waitUntil(() => !document.querySelector('.q-dialog'))
  expect(page().payload().shellPrefixes).toEqual({ read: [], write: [] })
  root.querySelector('[aria-label="Shell-Präfixe entfernen"]').click()
  await vi.waitFor(() => expect(page().payload().shellPrefixes).toBeNull())
})

it('finds an MCP tool by technical name and shows its description in the permission dialog', async () => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const resources = [
    { key: 'tool:search', label: 'Tickets suchen', toolName: 'search_tickets', description: 'Search ticket contents.' },
    { key: 'tool:lookup', label: 'Ticket abrufen', toolName: 'get_ticket', description: 'Read a ticket and its entries.' }
  ]
  await mount(IntegrationPermissionEditor, {
    modelValue: { ...rights(), resourceAvailability: {} },
    data: { inherited: Object.fromEntries(['private', 'public'].map((trust) => [trust, { actions: [action('mcp.call')], resources }])) }
  }, '/tools', 'settings-tools')
  root.querySelector('button').click()
  const input = await vi.waitUntil(() => document.querySelector('.q-dialog input[role="combobox"]'))
  input.focus()
  input.value = 'get_ticket'
  input.dispatchEvent(new Event('input', { bubbles: true }))
  const option = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent.includes('get_ticket')))
  option.click()
  await nextTick()
  expect(document.querySelector('.q-dialog').textContent).toContain('Read a ticket and its entries.')
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
    integrationId: 'calendar', name: 'Mein Kalender', providerType: 'caldav', permissions: { ...rights(), available: null, privatePermissions: { ...branch(), resourcePolicies: { 'calendar:work': { 'calendar.write': 'ask' } } } },
    inheritedAvailable: true, profiles: [{ id: builtinId, name: 'Allgemein', scope: 'instance' }],
    inherited: Object.fromEntries(['private', 'public'].map((trust) => [trust, { actions: [action('calendar.write')], resources: [{ key: 'calendar:work', label: 'Arbeit', available: true, applicableCapabilities: ['calendar.write'] }] }]))
  } }))
  vi.spyOn(integrations, 'fetchAccess').mockResolvedValue({})
  api.put.mockResolvedValue({ data: {} })
  const { page } = await mount(IntegrationPage, {}, '/integrations/calendar/resources', 'settings-integration-resources', pinia)
  await vi.waitFor(() => expect(page().permissionsData).toBeTruthy())
  await vi.waitUntil(() => root.querySelector('[aria-label="Arbeit: verfügbar"]'))
  root.querySelector('[aria-label="Arbeit: verfügbar"]').click()
  await nextTick()
  expect([page().permissions.resourceAvailability['calendar:work'], page().permissions.privatePermissions.resourcePolicies]).toEqual([false, { 'calendar:work': { 'calendar.write': 'ask' } }])
  await page().selectProfile(builtinId)
  page().permissions.privatePermissions.policies['calendar.write'] = 'auto'
  await nextTick()
  expect(root.textContent).toContain('Wirksam: Automatisch')
  await page().save()
  expect(api.put).toHaveBeenCalledWith('/integrations/calendar/permissions', expect.objectContaining({ privatePermissions: expect.objectContaining({ policies: { 'calendar.write': 'auto' } }) }), { params: { scope: 'personal', profile_id: builtinId } })
})
