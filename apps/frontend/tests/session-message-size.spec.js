// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import { Quasar } from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: {} }))

import SessionSettingsFields from '../src/features/chat/components/SessionSettingsFields.vue'

let app, root
afterEach(() => { app?.unmount(); root?.remove() })

it('changes message size while retaining the session configuration', async () => {
  const sessionProfile = { templateId: 'coding', agentIds: ['plan', 'build'], agentModels: { plan: { modelId: 'model' } }, messageSize: 'standard' }
  const update = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(SessionSettingsFields, {
    session: { sessionProfile }, fields: ['messageSize'], onUpdate: update
  }) })
  app.use(createPinia()).use(Quasar)
  app.mount(root)
  root.querySelector('.q-field__native').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('.q-item')].find((item) => item.textContent.trim() === 'Klein'))
  option.click()
  await nextTick()
  expect(update).toHaveBeenCalledWith({ session_profile: { ...sessionProfile, messageSize: 'small' } })
})
