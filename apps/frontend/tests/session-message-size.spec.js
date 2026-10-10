// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar, QSelect, QIcon, QItem, QItemLabel, QItemSection } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: {} }))

import SessionSettingsFields from '../src/features/chat/components/SessionSettingsFields.vue'
import { useProviderStore } from '../src/stores/providers'

let app, root
afterEach(() => { app?.unmount(); root?.remove() })

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
