// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import {
  Quasar, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
  QChip, QDialog, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel,
  QItemSection, QList, QSelect, QSeparator, QSpace, QTab, QTabPanel, QTabPanels,
  QTabs, QToggle, Ripple, ClosePopup, Dialog, Notify, copyToClipboard
} from 'quasar'

vi.mock('quasar', async () => ({
  ...await import('quasar/dist/quasar.client.js'),
  copyToClipboard: vi.fn().mockResolvedValue(undefined)
}))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), patch: vi.fn(), post: vi.fn() } }))

import ExecutionAgentSettingsPage from '../src/features/computers/ExecutionAgentSettingsPage.vue'
import { useExecutionAgentStore } from '../src/stores/execution-agents'
import { api } from 'src/boot/axios'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  document.querySelectorAll('.q-dialog').forEach(dialog => dialog.remove())
  vi.clearAllMocks()
})

function mountPage() {
  window.scrollTo = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  const pinia = createPinia()
  app = createApp({ render: () => h(ExecutionAgentSettingsPage) })
  app.use(pinia)
  app.use(Quasar, {
    components: {
      QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
      QChip, QDialog, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel,
      QItemSection, QList, QSelect, QSeparator, QSpace, QTab, QTabPanel, QTabPanels,
      QTabs, QToggle
    },
    directives: { Ripple, ClosePopup },
    plugins: { Dialog, Notify }
  })
  app.mount(root)
  return pinia
}

it('shares settings with creation, preserves tab drafts and copies the selected platform command', async () => {
  const agent = {
    id: 'computer-1', alias: 'Notebook', connected: true, pairingState: 'paired',
    agentVersion: '2026-10-07-bbbbbbbb', operations: ['maintenance.update', 'maintenance.update.interrupt'],
    homeDirectory: '/home/user', autoUpdateEnabled: null, sudoCredentialId: null
  }
  const payload = {
    items: [agent], backendVersion: '2026-10-08-aaaaaaaa', autoUpdateDefault: true,
    installationTarget: { version: '2026-10-08-aaaaaaaa', revision: 'a'.repeat(40) }
  }
  api.get.mockImplementation(async url => ({ data: url === '/personal-variables'
    ? [{ id: 'sudo-secret', name: 'Computer-Passwort', secret: true }, { id: 'plain', name: 'Normale Variable', secret: false }]
    : url.endsWith('pairing-policy')
    ? { defaultDurationSeconds: 1800, maximumDurationSeconds: 86400 } : payload }))
  const pinia = mountPage()
  const click = label => [...document.querySelectorAll('button, .q-item--clickable, .q-tab, .q-chip--clickable')]
    .find(element => element.textContent.includes(label)).click()
  await vi.waitFor(() => expect(root.textContent).toContain('Notebook'))
  expect(root.querySelector('.execution-agent-list-card').textContent).not.toContain('Funktionen')
  expect(root.textContent).not.toContain('Agent installieren')
  click('Verwalten')
  await vi.waitFor(() => expect(document.querySelector('input[aria-label="Alias (optional)"]').value).toBe('Notebook'))
  expect(document.querySelector('.q-tab--active').textContent).toBe('Einstellungen')
  expect(document.querySelector('.q-dialog').textContent).toContain('Instanzstandard (aktuell an)')
  document.querySelector('[aria-label="sudo-Passwort"]').click()
  await vi.waitFor(() => expect(document.querySelector('.q-menu')?.textContent).toContain('Computer-Passwort'))
  expect(document.querySelector('.q-menu').textContent).not.toContain('Normale Variable')
  ;[...document.querySelectorAll('.q-menu .q-item__label')].find(item => item.textContent === 'Computer-Passwort').click()
  await vi.waitFor(() => expect(document.querySelector('.q-menu')).toBeNull())
  document.querySelector('[aria-label="Automatische Agent-Updates"]').click()
  await vi.waitFor(() => expect(document.querySelector('.q-menu')).not.toBeNull())
  const offOption = [...document.querySelectorAll('.q-menu .q-item__label')].find(item => item.textContent === 'Aus')
  offOption.click()
  const alias = document.querySelector('input[aria-label="Alias (optional)"]')
  alias.value = 'Notebook bearbeitet'
  alias.dispatchEvent(new Event('input', { bubbles: true }))
  click('Aktionen')
  await vi.waitFor(() => expect(document.querySelector('.q-dialog').textContent).toContain('älterer Commit'))
  expect(document.querySelector('.q-dialog').textContent).not.toContain('Agent deinstallieren')
  click('Agent aktualisieren')
  await vi.waitFor(() => expect(document.body.textContent).toContain('auch aus anderen Chats'))
  ;[...document.querySelectorAll('.q-dialog button')].find(button => button.textContent.trim() === 'Abbrechen').click()
  await vi.waitFor(() => expect(document.querySelectorAll('.q-dialog')).toHaveLength(1))
  expect(api.post).not.toHaveBeenCalled()
  api.post.mockResolvedValue({ data: { maintenance: null } })
  click('Agent aktualisieren')
  await vi.waitFor(() => expect(document.body.textContent).toContain('auch aus anderen Chats'))
  ;[...document.querySelectorAll('.q-dialog button')].find(button => button.textContent.trim() === 'Aktualisieren').click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith('/execution-agents/computer-1/maintenance', { action: 'update' }, { suppressErrorToast: true }))
  await vi.waitFor(() => expect(document.querySelectorAll('.q-dialog')).toHaveLength(1))

  payload.items = [{ ...agent, alias: 'Notebook aktualisiert', agentVersion: payload.backendVersion }]
  await useExecutionAgentStore(pinia).fetchAll()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog').textContent).toContain('Gleicher Stand wie MatoLoop'))
  expect(document.querySelector('.q-dialog').textContent).toContain('Notebook aktualisiert')
  click('Einstellungen')
  await vi.waitFor(() => expect(document.querySelector('input[aria-label="Alias (optional)"]').value).toBe('Notebook bearbeitet'))
  expect(document.querySelectorAll('.q-dialog')).toHaveLength(1)
  api.patch.mockResolvedValue({ data: { ...payload.items[0], alias: 'Notebook bearbeitet', sudoCredentialId: 'sudo-secret' } })
  click('Änderungen speichern')
  await vi.waitFor(() => expect(api.patch).toHaveBeenCalledWith('/execution-agents/computer-1', expect.objectContaining({ alias: 'Notebook bearbeitet', autoUpdateEnabled: false, sudoCredentialId: 'sudo-secret' })))
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')).toBeNull())
  click('Verwalten')
  await vi.waitFor(() => expect(document.querySelector('.q-tab--active').textContent).toBe('Einstellungen'))
  document.querySelector('[aria-label="sudo-Passwort"]').click()
  await vi.waitFor(() => expect(document.querySelector('.q-menu')?.textContent).toContain('Kein Secret zugeordnet'))
  ;[...document.querySelectorAll('.q-menu .q-item__label')].find(item => item.textContent === 'Kein Secret zugeordnet').click()
  api.patch.mockResolvedValue({ data: { ...payload.items[0], sudoCredentialId: null } })
  click('Änderungen speichern')
  await vi.waitFor(() => expect(api.patch).toHaveBeenLastCalledWith('/execution-agents/computer-1', expect.objectContaining({ sudoCredentialId: null })))
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')).toBeNull())
  click('Computer hinzufügen')
  await vi.waitFor(() => expect(document.body.textContent).toContain('Gültigkeitsdauer des Kopplungstokens'))
  expect(document.querySelector('input[aria-label="Alias (optional)"]').value).toBe('')
  expect(document.querySelector('.q-tabs')).toBeNull()
  const command = () => document.querySelector('textarea[aria-label="Installationsbefehl"]').value
  expect(command()).toContain('/api/execution-agents/install.sh')
  document.querySelector('button[aria-label="Installationsbefehl kopieren"]').click()
  await vi.waitFor(() => expect(copyToClipboard).toHaveBeenLastCalledWith(command()))
  const newAlias = document.querySelector('input[aria-label="Alias (optional)"]')
  newAlias.value = 'Neuer Computer'
  newAlias.dispatchEvent(new Event('input', { bubbles: true }))
  click('Windows')
  await vi.waitFor(() => expect(command()).toContain('/api/execution-agents/install.ps1'))
  click('Windows')
  expect(document.querySelectorAll('.q-chip[aria-pressed="true"]')).toHaveLength(1)
  expect(command()).not.toContain('/api/execution-agents/install.sh')
  expect(document.querySelector('input[aria-label="Alias (optional)"]').value).toBe('Neuer Computer')
  document.querySelector('button[aria-label="Installationsbefehl kopieren"]').click()
  await vi.waitFor(() => expect(copyToClipboard).toHaveBeenLastCalledWith(command()))
  newAlias.value = ''
  newAlias.dispatchEvent(new Event('input', { bubbles: true }))
  const created = { id: '12345678-0000-4000-8000-000000000000', alias: '', displayName: 'Computer 12345678', pairingState: 'pending' }
  api.post.mockResolvedValue({ data: { agent: created, pairingToken: 'new-token', websocketUrl: '/api/execution-agents/connect' } })
  document.querySelector('.q-dialog button[type="submit"]').click()
  await vi.waitFor(() => expect(api.post).toHaveBeenCalledWith('/execution-agents', expect.objectContaining({ alias: '' })))
  await vi.waitFor(() => expect(root.textContent).toContain('Computer 12345678'))
})

it('confirms eligible bulk updates once, continues after errors and uses the existing sudo prompt', async () => {
  const currentVersion = '2026-10-08-aaaaaaaa'
  const agent = { connected: true, pairingState: 'paired', agentVersion: '2026-10-07-bbbbbbbb', operations: ['maintenance.start', 'maintenance.update.interrupt'] }
  const running = { action: 'update', status: 'running', startedAt: new Date().toISOString() }
  const items = [
    { ...agent, id: 'desktop', alias: 'Desktop' },
    { ...agent, id: 'laptop', alias: 'Laptop' },
    { ...agent, id: 'current', alias: 'Aktuell', agentVersion: currentVersion },
    { ...agent, id: 'offline', alias: 'Offline', connected: false },
    { ...agent, id: 'unsupported', alias: 'Ohne Updates', operations: [] },
    { ...agent, id: 'busy', alias: 'Wartung', maintenance: running },
    { ...agent, id: 'unknown', alias: 'Unbestätigt', maintenance: { ...running, status: 'unknown' } }
  ]
  api.get.mockImplementation(async url => ({ data: url.endsWith('pairing-policy')
    ? { defaultDurationSeconds: 1800, maximumDurationSeconds: 86400 }
    : { items, backendVersion: currentVersion, installationTarget: null } }))
  const store = useExecutionAgentStore(mountPage())
  await vi.waitFor(() => expect(store.loaded).toBe(true))
  expect(root.querySelector('header').textContent).not.toContain('Agents aktualisieren')
  store.installationTarget = { version: currentVersion, revision: 'a'.repeat(40) }
  const updateButton = () => [...root.querySelectorAll('header button')].find(button => button.textContent.includes('Agents aktualisieren'))
  await vi.waitFor(() => expect(updateButton()?.textContent).toContain('(2)'))
  expect(root.querySelector('header button')).toBe(updateButton())
  updateButton().click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')?.textContent).toContain('„Desktop“, „Laptop“'))
  expect(document.querySelector('.q-dialog').textContent).toContain('auch aus anderen Chats')
  const dialogButton = label => [...document.querySelectorAll('.q-dialog button')].find(button => button.textContent.trim() === label)
  dialogButton('Abbrechen').click()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')).toBeNull())
  expect(api.post).not.toHaveBeenCalled()

  api.post.mockRejectedValueOnce({ response: { data: { error: { code: 'maintenance.failed' } } } })
    .mockRejectedValueOnce({ response: { data: { error: { code: 'process.sudoAuthRequired' } } } })
    .mockResolvedValueOnce({ data: { maintenance: running } })
  updateButton().click()
  await vi.waitFor(() => expect(dialogButton('Aktualisieren')).toBeTruthy())
  dialogButton('Aktualisieren').click()
  await vi.waitFor(() => expect(document.querySelector('input[type="password"]')).not.toBeNull())
  expect(updateButton().disabled).toBe(true)
  expect(document.body.textContent).toContain('Desktop:')
  const password = document.querySelector('input[type="password"]')
  expect(password.closest('.q-dialog').textContent).toContain('„Laptop“')
  password.value = 'one-time password'
  password.dispatchEvent(new Event('input', { bubbles: true }))
  await vi.waitFor(() => expect(dialogButton('Wartung starten').disabled).toBe(false))
  dialogButton('Wartung starten').click()
  await vi.waitFor(() => expect(document.body.textContent).toContain('Updates für 1 Agent gestartet'))
  expect(api.post.mock.calls.map(([url, body]) => [url, body])).toEqual([
    ['/execution-agents/desktop/maintenance', { action: 'update' }],
    ['/execution-agents/laptop/maintenance', { action: 'update' }],
    ['/execution-agents/laptop/maintenance', { action: 'update', sudoPassword: 'one-time password' }]
  ])
})
