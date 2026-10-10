// @vitest-environment jsdom
import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import {
  Quasar, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox,
  QChip, QDialog, QForm, QIcon, QInnerLoading, QInput, QItem, QItemLabel,
  QItemSection, QList, QSelect, QSeparator, QSpace, QToggle, Ripple, ClosePopup
} from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))

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

it('keeps management details live and opens the shared editor for editing and adding', async () => {
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
      QItemSection, QList, QSelect, QSeparator, QSpace, QToggle
    },
    directives: { Ripple, ClosePopup }
  })
  app.mount(root)
  const click = label => [...document.querySelectorAll('button, .q-item--clickable')]
    .find(element => element.textContent.includes(label)).click()
  await vi.waitFor(() => expect(root.textContent).toContain('Notebook'))
  expect(root.querySelector('.execution-agent-list-card').textContent).not.toContain('Funktionen')
  click('Verwalten')
  await vi.waitFor(() => expect(document.querySelector('.q-dialog').textContent).toContain('älterer Commit'))
  expect(document.querySelector('.q-dialog').textContent).not.toContain('Agent deinstallieren')

  payload.items = [{ ...agent, alias: 'Notebook aktualisiert', agentVersion: payload.backendVersion }]
  await useExecutionAgentStore(pinia).fetchAll()
  await vi.waitFor(() => expect(document.querySelector('.q-dialog').textContent).toContain('Gleicher Stand wie MatoLoop'))
  expect(document.querySelector('.q-dialog').textContent).toContain('Notebook aktualisiert')
  click('Computer bearbeiten')
  await vi.waitFor(() => expect(document.body.textContent).toContain('Änderungen speichern'))
  expect(document.querySelector('input[aria-label="Alias"]').value).toBe('Notebook aktualisiert')
  click('Abbrechen')
  await vi.waitFor(() => expect(document.body.textContent).not.toContain('Änderungen speichern'))
  click('Schließen')
  click('Computer hinzufügen')
  await vi.waitFor(() => expect(document.body.textContent).toContain('Gültigkeitsdauer des Kopplungstokens'))
  expect(document.querySelector('input[aria-label="Alias"]').value).toBe('')
})
