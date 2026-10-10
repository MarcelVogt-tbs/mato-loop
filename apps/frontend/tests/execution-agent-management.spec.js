// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import {
  Quasar, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
  QChip, QDialog, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel,
  QItemSection, QList, QSelect, QSeparator, QSpace, QTab, QTabPanel, QTabPanels,
  QTabs, QToggle, Ripple, ClosePopup, Notify, copyToClipboard
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

it('shares settings with creation, preserves tab drafts and copies the selected platform command', async () => {
  const agent = {
    id: 'computer-1', alias: 'Notebook', connected: true, pairingState: 'paired',
    agentVersion: '2026-10-07-bbbbbbbb', operations: ['maintenance.update'],
    homeDirectory: '/home/user'
  }
  const payload = {
    items: [agent], backendVersion: '2026-10-08-aaaaaaaa',
    installationTarget: { version: '2026-10-08-aaaaaaaa', revision: 'a'.repeat(40) }
  }
  api.get.mockImplementation(async url => ({ data: url.endsWith('pairing-policy')
    ? { defaultDurationSeconds: 1800, maximumDurationSeconds: 86400 } : payload }))
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
    plugins: { Notify }
  })
  app.mount(root)
  const click = label => [...document.querySelectorAll('button, .q-item--clickable, .q-tab, .q-chip--clickable')]
    .find(element => element.textContent.includes(label)).click()
  await vi.waitFor(() => expect(root.textContent).toContain('Notebook'))
  expect(root.querySelector('.execution-agent-list-card').textContent).not.toContain('Funktionen')
  expect(root.textContent).not.toContain('Agent installieren')
  click('Verwalten')
  await vi.waitFor(() => expect(document.querySelector('input[aria-label="Alias (optional)"]').value).toBe('Notebook'))
  expect(document.querySelector('.q-tab--active').textContent).toBe('Einstellungen')
  const alias = document.querySelector('input[aria-label="Alias (optional)"]')
  alias.value = 'Notebook bearbeitet'
  alias.dispatchEvent(new Event('input', { bubbles: true }))
  click('Aktionen')
  await vi.waitFor(() => expect(document.querySelector('.q-dialog').textContent).toContain('älterer Commit'))
  expect(document.querySelector('.q-dialog').textContent).not.toContain('Agent deinstallieren')

  payload.items = [{ ...agent, alias: 'Notebook aktualisiert', agentVersion: payload.backendVersion }]
  await useExecutionAgentStore(pinia).fetchAll()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog').textContent).toContain('Gleicher Stand wie MatoLoop'))
  expect(document.querySelector('.q-dialog').textContent).toContain('Notebook aktualisiert')
  click('Einstellungen')
  await vi.waitFor(() => expect(document.querySelector('input[aria-label="Alias (optional)"]').value).toBe('Notebook bearbeitet'))
  expect(document.querySelectorAll('.q-dialog')).toHaveLength(1)
  api.patch.mockResolvedValue({ data: { ...payload.items[0], alias: 'Notebook bearbeitet' } })
  click('Änderungen speichern')
  await vi.waitFor(() => expect(api.patch).toHaveBeenCalledWith('/execution-agents/computer-1', expect.objectContaining({ alias: 'Notebook bearbeitet' })))
  await vi.waitFor(() => expect(document.querySelector('.q-dialog')).toBeNull())
  click('Verwalten')
  await vi.waitFor(() => expect(document.querySelector('.q-tab--active').textContent).toBe('Einstellungen'))
  click('Abbrechen')
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
