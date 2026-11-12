// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Quasar } from 'quasar'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() } }))
vi.mock('src/features/browser/BrowserSetupDialog.vue', () => ({ default: { render: () => null } }))
const { confirm } = vi.hoisted(() => ({ confirm: vi.fn() }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))

import Dialog from '../src/features/integrations/IntegrationCreateDialog.vue'
import Editor from '../src/features/integrations/IntegrationEditorPage.vue'
import SignatureEditor from '../src/features/mail/MailSignatureEditor.vue'
import { useAuthStore } from '../src/stores/auth'
import { useIntegrationStore } from '../src/stores/integrations'
import { api } from 'src/boot/axios'

let app, root, accounts
const account = { id: 'calendar-1', integrationType: 'calendar', providerType: 'caldav', name: 'Kalender', enabled: true, hasCredential: true, config: { baseUrl: 'https://calendar.test', username: 'owner' } }
const adapters = [{ integrationType: 'calendar', adapterType: 'caldav', executionLocation: 'backend', availability: 'full' }]
beforeEach(() => {
  vi.resetAllMocks()
  document.execCommand = vi.fn()
  confirm.mockResolvedValue(true)
  accounts = [{ ...account }]
  api.get.mockImplementation(async (url) => ({ data: url === '/integrations/catalog' ? { adapters } : url.endsWith('/permissions') ? {
    name: account.name, providerType: 'caldav', profiles: [],
    permissions: { privatePermissions: { policies: {}, resourcePolicies: {} }, publicPermissions: { policies: {}, resourcePolicies: {} } },
    inherited: Object.fromEntries(['private', 'public'].map((trust) => [trust, { actions: [{ capability: 'calendar.read', label: 'Kalender lesen', policy: 'ask' }], resources: [] }]))
  } : {
    providers: [], accounts: url === '/integrations/calendar' ? accounts : [], bases: [], instances: []
  } }))
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})
afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.restoreAllMocks()
  delete document.execCommand
})

function mount(render, pinia, router) {
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render }).use(pinia).use(Quasar, quasarOptions)
  if (router) app.use(router)
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
}

async function mountEditor(scope, create = false, type = 'calendar') {
  const pinia = createPinia()
  useAuthStore(pinia).isAdmin = true
  const prefix = scope === 'instance' ? 'admin' : 'settings'
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/integrations', name: `${prefix}-integrations`, component: { render: () => null } },
    { path: '/integrations/new', name: `${prefix}-integration-create`, component: Editor },
    { path: '/admin/tools', name: 'admin-tools', component: { render: () => null } },
    { path: '/integrations/:integrationId/edit', name: `${prefix}-integration-edit`, component: Editor }
  ] })
  await router.push(create ? { name: `${prefix}-integration-create`, query: { type, name: 'Neu' } } : `/integrations/${account.id}/edit`)
  let page
  mount(() => h(RouterView, null, { default: ({ Component }) => Component && h(Component, { ref: (value) => { page = value?.$?.setupState } }) }), pinia, router)
  await vi.waitUntil(() => root.querySelector('form'))
  return { router, page: () => page }
}

function button(label) {
  return [...document.querySelectorAll('button')].find((item) => item.textContent.includes(label))
}

async function fill(label, value) {
  const field = [...root.querySelectorAll('.q-field')].find((item) => item.querySelector('.q-field__label')?.textContent === label)
  const input = field.querySelector('input')
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

it('collects only basics without creating a connection', async () => {
  const pinia = createPinia()
  const integrations = useIntegrationStore(pinia)
  integrations.selectScope('personal')
  integrations.catalog = adapters
  const proceed = vi.fn()
  mount(() => h(Dialog, { modelValue: true, scope: 'personal', onContinue: proceed }), pinia)
  await vi.waitUntil(() => document.querySelector('.integration-dialog'))
  button('Weiter').click()
  await vi.waitFor(() => expect(proceed).toHaveBeenCalledWith({ type: 'calendar', name: 'CalDAV' }))
  expect(api.post).not.toHaveBeenCalled()
})

it('pastes Outlook tables and formatting without loading missing images or keeping active styles', async () => {
  mount(() => h(SignatureEditor, { modelValue: '' }), createPinia())
  expect(root.textContent).not.toContain('Füge deine Signatur aus Outlook oder Word direkt ein.')
  root.querySelector('[aria-label="Hinweise zur E-Mail-Signatur"]').click()
  await vi.waitUntil(() => document.getElementById('signature-info-title'))
  const info = document.getElementById('signature-info-title').closest('.q-card')
  expect(info.textContent).toContain('höchstens 8 Bilder')
  info.querySelector('[aria-label="Dialog schließen"]').click()
  await vi.waitUntil(() => !document.getElementById('signature-info-title'))
  const paste = new Event('paste', { bubbles: true, cancelable: true })
  const images = '<img src="data:image/png;base64, iVBORw0KGgo= ">'.repeat(7)
  Object.defineProperty(paste, 'clipboardData', { value: { getData: () => `
    <table class="MsoNormalTable" cellpadding="0" cellspacing="0" style="border-collapse:collapse;mso-padding-alt:0">
      <tr><td valign="top">${images}<img src="file:///C:/signature/logo.png" alt="Logo"></td>
      <td style="border-left:2px solid #800080;padding-left:6px;color:#800080;font-size:11pt;position:fixed;background-image:url(https://example.com/track.png)">
        <font face="Arial" color="#800080"><b>Team</b></font>
      </td></tr>
    </table>` } })
  root.querySelector('[contenteditable]').dispatchEvent(paste)
  const insertions = document.execCommand.mock.calls.filter(([command]) => command === 'insertHTML')
  expect(insertions).toHaveLength(1)
  const inserted = insertions[0][2]
  expect(inserted).toContain('<table cellpadding="0" cellspacing="0" style="border-collapse:collapse">')
  expect(inserted).toContain('border-left:2px solid #800080;padding-left:6px;color:#800080;font-size:11pt')
  expect(inserted).toContain('<font face="Arial" color="#800080"><b>Team</b></font>')
  expect(inserted).not.toMatch(/file:|url\(|position:|mso-|class=/)
  expect(inserted.match(/<img src="data:image\/png;base64,iVBORw0KGgo=">/g)).toHaveLength(7)
  expect(app.config.globalProperties.$q.notify).toHaveBeenCalledWith(expect.objectContaining({ type: 'warning' }))
})

it.each(['device_code', 'browser'])('saves Microsoft 365 %s settings before offering login', async (flow) => {
  const originalGet = api.get.getMockImplementation()
  let connection = null
  api.get.mockImplementation(async (url, ...args) => {
    if (url === '/integrations/catalog') return { data: { adapters: [
      ...adapters, { integrationType: 'microsoft365', adapterType: 'microsoft_graph', executionLocation: 'backend', availability: 'full', personalOnly: true }
    ] } }
    if (url === '/integrations/microsoft365') return { data: { accounts: connection ? [connection] : [] } }
    return originalGet(url, ...args)
  })
  api.post.mockImplementation(async (_url, payload) => {
    connection = { id: 'microsoft365', integrationType: 'microsoft365', providerType: 'microsoft_graph', ...payload, hasClientSecret: flow === 'browser' }
    return { data: { status: 'ok' } }
  })
  const { router } = await mountEditor('personal', true, 'microsoft365')
  await fill('Mandanten-ID (Tenant-ID)', '11111111-1111-4111-8111-111111111111')
  await fill('Anwendungs-ID (Client-ID)', '22222222-2222-4222-8222-222222222222')
  if (flow === 'browser') {
    root.querySelector('[role="combobox"]').click()
    let option
    await vi.waitFor(() => {
      option = [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent.includes('Browser-Anmeldung'))
      expect(option).toBeTruthy()
    })
    option.click()
    await nextTick()
    await fill('Client-Secret', 'test-client-secret')
    for (const label of ['E-Mail-Entwürfe in Outlook erstellen', 'E-Mails senden', 'E-Mails und Entwürfe löschen', 'Kalender lesen', 'Termine erstellen und ändern', 'Termine löschen', 'Microsoft To Do lesen', 'Aufgaben, Listen und Anhänge erstellen und ändern', 'Aufgaben, Listen und Anhänge löschen']) {
      [...root.querySelectorAll('.q-toggle')].find((item) => item.textContent.includes(label)).click()
    }
    await nextTick()
  }
  expect(root.textContent).not.toContain('Öffentliche Clientflows zulassen')
  root.querySelector('[aria-label="Hinweise zur Microsoft-365-Einrichtung"]').click()
  await vi.waitUntil(() => document.getElementById('microsoft-info-title'))
  const info = document.getElementById('microsoft-info-title').closest('.q-card')
  expect(info.textContent).toContain(flow === 'device_code' ? 'Öffentliche Clientflows zulassen' : '/api/integrations/microsoft365/oauth/callback')
  expect(info.textContent).toContain('Mail.ReadWrite')
  info.querySelector('[aria-label="Dialog schließen"]').click()
  await vi.waitUntil(() => !document.getElementById('microsoft-info-title'))
  expect(button('Bei Microsoft anmelden')).toBeUndefined()
  button('Speichern').click()
  await vi.waitFor(() => expect(router.currentRoute.value.params.integrationId).toBe('microsoft365'))
  expect(api.post).toHaveBeenCalledWith('/integrations/microsoft365', {
    name: 'Neu', enabled: true,
    config: { tenant_id: '11111111-1111-4111-8111-111111111111', client_id: '22222222-2222-4222-8222-222222222222', auth_flow: flow, allow_drafts: flow === 'browser', allow_send: flow === 'browser', allow_delete: flow === 'browser', signature_html: '', read_calendar: flow === 'browser', write_calendar: flow === 'browser', delete_calendar: flow === 'browser', read_tasks: flow === 'browser', write_tasks: flow === 'browser', delete_tasks: flow === 'browser' },
    ...(flow === 'browser' ? { client_secret: 'test-client-secret' } : {})
  }, { params: { scope: 'personal' } })
  await vi.waitFor(() => expect(button('Bei Microsoft anmelden')).toBeTruthy())
})

it.each(['instance', 'personal'])('edits and tests the saved %s connection without replacing its password', async (scope) => {
  api.put.mockImplementation(async (_url, payload) => {
    accounts = [{ ...account, name: payload.name }]
    return { data: {} }
  })
  api.post.mockResolvedValue({ data: { status: 'ok' } })
  await mountEditor(scope)
  await fill('Name', 'Umbenannt')
  expect(button('Verbindung testen').disabled).toBe(true)
  button('Speichern').click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledWith(`/integrations/calendar/accounts/${account.id}`, expect.objectContaining({ name: 'Umbenannt' }), { params: { scope } }))
  expect(api.put.mock.calls[0][1]).not.toHaveProperty('password')
  await vi.waitUntil(() => !button('Verbindung testen').disabled)
  button('Verbindung testen').click()
  await vi.waitFor(() => expect(root.textContent).toContain('Verbindung erfolgreich'))
  expect(api.post).toHaveBeenCalledWith(`/integrations/calendar/accounts/${account.id}/test`, null, { params: { scope } })
})

it('creates a connection only after completing the page and opens its editor', async () => {
  api.post.mockImplementation(async (_url, payload) => {
    accounts.push({ ...account, id: 'new-calendar', name: payload.name })
    return { data: {} }
  })
  const { router } = await mountEditor('personal', true)
  await fill('CalDAV-Basis-URL', 'https://calendar.test')
  await fill('Benutzername', 'owner')
  await fill('Passwort', 'test-password')
  button('Speichern').click()
  await vi.waitFor(() => expect(router.currentRoute.value.params.integrationId).toBe('new-calendar'))
})

it('creates an instance-wide Jina connection without requiring an API key', async () => {
  const originalGet = api.get.getMockImplementation()
  const readers = []
  api.get.mockImplementation(async (url) => url === '/integrations/catalog'
    ? { data: { adapters: [...adapters, { integrationType: 'webfetch', adapterType: 'jina', executionLocation: 'backend', availability: 'instance_only', instanceOnly: true }] } }
    : url === '/integrations/webfetch' ? { data: { providers: readers, integrationId: null } } : originalGet(url))
  api.post.mockImplementation(async (_url, payload) => {
    readers.push({ id: 'reader', name: payload.name, integrationType: 'webfetch', providerType: 'jina', enabled: true, config: { baseUrl: payload.base_url } })
    return { data: {} }
  })
  const { router } = await mountEditor('instance', true, 'webfetch')
  await fill('Jina Reader-Basis-URL', 'https://jina.devmato.net')
  await fill('Standardzeitlimit', '60')
  ;[...root.querySelectorAll('[role="switch"]')].find(item => item.textContent.includes('Overlays standardmäßig entfernen')).click()
  button('Cookie hinzufügen').click()
  await nextTick()
  await fill('Zielhost', 'www.golem.de')
  await fill('Cookie-Name', 'consent')
  await fill('Cookie-Wert', 'test-consent')
  button('Speichern').click()
  await vi.waitFor(() => expect(router.currentRoute.value.params.integrationId).toBe('reader'))
  expect(api.post).toHaveBeenCalledWith('/integrations/webfetch/providers', { name: 'Neu', base_url: 'https://jina.devmato.net', api_key: null, enabled: true, timeout_seconds: 60, remove_overlay: true, cookies: [{ domain: 'www.golem.de', name: 'consent', value: 'test-consent' }] })
})

it('retains a failed save and allows cancelling navigation with an unsaved draft', async () => {
  api.put.mockRejectedValue(new Error('offline'))
  confirm.mockResolvedValue(false)
  const { router, page } = await mountEditor('personal')
  await fill('Name', 'Entwurf')
  button('Speichern').click()
  await vi.waitUntil(() => api.put.mock.calls.length && !page().saving)
  await router.push('/integrations')
  expect([router.currentRoute.value.params.integrationId, page().form.name, page().dirty]).toEqual([account.id, 'Entwurf', true])
})

it('retains both tab drafts and saves permissions through the shared header', async () => {
  api.put.mockResolvedValue({ data: {} })
  const { router, page } = await mountEditor('personal')
  const tab = (label) => [...root.querySelectorAll('[role="tab"]')].find((item) => item.textContent.includes(label)).click()
  await fill('Name', 'Entwurf')
  tab('Berechtigungen')
  await vi.waitUntil(() => button('Eigene Regel hinzufügen'))
  button('Eigene Regel hinzufügen').click()
  const choose = async (label, option) => {
    const field = await vi.waitUntil(() => [...document.querySelectorAll('.q-dialog .q-field')].find(item => item.querySelector('.q-field__label')?.textContent === label))
    field.querySelector('[role="combobox"]').click()
    const choice = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find(item => item.querySelector('.q-item__label')?.textContent === option))
    choice.click()
    await vi.waitUntil(() => !document.querySelector('[role="option"]'))
  }
  await choose('Aktion', 'Kalender lesen')
  await choose('Berechtigung', 'Automatisch')
  button('Übernehmen').click()
  await vi.waitUntil(() => !document.querySelector('.q-dialog'))
  tab('Einstellungen')
  await vi.waitUntil(() => router.currentRoute.value.query.tab === 'settings')
  expect(page().form.name).toBe('Entwurf')
  tab('Berechtigungen')
  await vi.waitUntil(() => router.currentRoute.value.query.tab === 'permissions')
  button('Speichern').click()
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledExactlyOnceWith(`/integrations/${account.id}/permissions`, expect.objectContaining({ privatePermissions: { policies: { 'calendar.read': 'auto' }, resourcePolicies: {} } }), { params: { scope: 'personal', profile_id: null } }))
})
