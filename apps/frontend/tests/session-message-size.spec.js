// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QSelect, QIcon, QItem, QItemLabel, QItemSection } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn() } }))

import SessionSettingsFields from '../src/features/chat/components/SessionSettingsFields.vue'
import { useProviderStore } from '../src/stores/providers'
import { useExecutionAgentStore } from '../src/stores/execution-agents'
import { api } from 'src/boot/axios'
import { quasarOptions } from './quasar-options'

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.restoreAllMocks(); vi.clearAllMocks() })

it('changes message size while retaining configuration and shows an unselected computer as information', async () => {
  const sessionProfile = { templateId: 'coding', agentIds: ['plan', 'build'], agentModels: { plan: { modelId: 'model' } }, messageSize: 'standard' }
  const update = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: { sessionProfile }, fields: ['messageSize', 'computer'], showWarning: true, onUpdate: update
  }) })
  app.use(createPinia()).use(Quasar, { components: { QSelect, QIcon, QItem, QItemLabel, QItemSection } })
  app.mount(root)
  expect(root.querySelector('.q-banner[role="status"] .text-info')?.textContent).toBe('info_outline')
  root.querySelector('.q-field__native').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find((item) => item.querySelector('.q-item__label')?.textContent === 'Klein'))
  option.click()
  await nextTick()
  expect(update).toHaveBeenCalledWith({ session_profile: { ...sessionProfile, messageSize: 'small' } })
})

it.each(['override', 'removed'])('lets the compact model menu return to the inherited model from %s', async (modelId) => {
  const update = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: { agentProfileId: 'agent', sessionProfile: { agentModels: { agent: { modelId, reasoningEffort: 'high' } } } },
    fields: ['model'], compact: true, onUpdate: update
  }) })
  app.use(createPinia()).use(Quasar, { components: { QSelect, QIcon, QItem, QItemLabel, QItemSection } })
  const providers = useProviderStore()
  providers.providers = [{ id: 'provider', name: 'Local', enabled: true }]
  providers.modelDefaults = { defaultModelId: 'default' }
  providers.models = [
    { id: 'override', providerId: 'provider', modelId: 'chat', enabled: true, reasoningEfforts: ['high'] },
    { id: 'default', providerId: 'provider', modelId: 'fallback', enabled: true, reasoningEfforts: ['low'] }
  ]
  app.mount(root)
  root.querySelector('.composer-model .q-field__native').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')]
    .find((item) => item.textContent.includes('Standardmodell verwenden')))
  option.click()
  await nextTick()
  expect(update).toHaveBeenCalledWith({ session_profile: { agentModels: { agent: { modelId: null, reasoningEffort: null } } } })
})

it('browses the selected computer from the session directory field', async () => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  api.get.mockImplementation(async (_url, { params }) => ({ data: {
    path: params.path, hasMore: false, nextOffset: null,
    entries: params.path === '/workspace/project' ? [{ name: 'src', path: '/workspace/project/src', kind: 'directory' }] : []
  } }))
  const pinia = createPinia()
  useExecutionAgentStore(pinia).items = [{ id: 'computer', alias: 'Server', connected: true, homeDirectory: '/home/user', operations: ['filesystem.list'] }]
  const update = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: { id: 'session', preferredExecutionAgentId: 'computer', currentDirectory: '/workspace/project' },
    fields: ['computer'], grouped: true, onUpdate: update
  }) }).use(pinia).use(Quasar, quasarOptions)
  app.mount(root)
  root.querySelector('[aria-label="Arbeitsverzeichnis auswählen"]').click()
  const folder = await vi.waitUntil(() => [...document.querySelectorAll('.q-dialog .q-item')].find(item => item.textContent.includes('/workspace/project/src')))
  folder.click()
  await vi.waitUntil(() => document.querySelector('.q-dialog')?.textContent.includes('Keine Unterordner vorhanden.'))
  ;[...document.querySelectorAll('.q-dialog button')].find(button => button.textContent.includes('Diesen Ordner auswählen')).click()
  expect(update).toHaveBeenCalledWith({ current_directory: '/workspace/project/src' })
})
