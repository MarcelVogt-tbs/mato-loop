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

import Page from '../src/features/settings/ModelDefaultsPage.vue'
import { api } from 'src/boot/axios'
import { useProviderStore } from '../src/stores/providers'

let app, root
afterEach(() => { app?.unmount(); root?.remove(); vi.clearAllMocks() })

it('edits and resets profile overrides while retaining the standard models', async () => {
  const defaults = {
    configured: { defaultModelId: 'default', titleGenerationModelId: 'title', imageGenerationModelId: 'images', imageEditingModelId: null, profileModels: {} },
    inherited: { defaultModelId: 'default', imageEditingModelId: 'edits', profileModels: { plan: { modelId: 'profile', reasoningEffort: 'low' } } },
    profiles: [{ id: 'plan', name: 'Plan' }], personalSettingsEnabled: true
  }
  const inventory = {
    providers: [{ id: 'provider', name: 'Provider', enabled: true }],
    models: [
      ...['default', 'profile', 'title'].map((id) => ({ id, name: id, modelId: id, providerId: 'provider', enabled: true, supportsTools: true, reasoningEffort: 'high', reasoningEfforts: ['none', 'low', 'high'] })),
      ...[['images', ['image_generation']], ['edits', ['image_editing']]].map(([id, capabilities]) => ({ id, name: id, modelId: id, providerId: 'provider', enabled: true, capabilities }))
    ]
  }
  api.get.mockImplementation(async (url) => ({ data: url === '/model-defaults' ? defaults : inventory }))
  api.put.mockImplementation(async (_url, configured) => ({ data: { ...defaults, configured: JSON.parse(JSON.stringify(configured)) } }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/models', component: Page }] })
  await router.push('/models?profileId=plan')
  root = document.createElement('div')
  document.body.append(root)
  let page
  app = createApp({ render: () => h(RouterView, null, { default: ({ Component }) => h(Component, { ref: (instance) => { page = instance?.$?.setupState } }) }) })
  app.use(createPinia()).use(router).use(Quasar, quasarOptions)
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await vi.waitFor(() => expect(page?.ready).toBe(true))

  expect(root.textContent).toContain('Agentenprofil')
  expect(root.textContent).toContain('Effektiv: profile')
  expect(root.textContent).not.toContain('Komprimierung')
  page.setChoice({ modelId: 'profile', reasoningEffort: 'high' })
  page.profileId = null
  await nextTick()
  expect(root.textContent).toContain('Komprimierung')
  expect(root.textContent).toContain('Memory / Dreaming')
  expect(root.textContent).toContain('Bilder')
  const editingField = page.fields.find((field) => field.key === 'imageEditingModelId')
  expect(page.options(editingField).map((option) => option.value)).toEqual([null, 'edits'])
  expect(page.effectiveLabel(editingField)).toBe('Effektiv: edits · Provider')
  page.form.imageEditingModelId = 'edits'
  const memoryField = [...root.querySelectorAll('.q-field')].find((field) => field.textContent.includes('Memory / Dreaming'))
  expect(memoryField.parentElement.textContent).toContain('Effektiv: Hoch')
  page.setDreamingChoice({ modelId: 'default', reasoningEffort: 'high' })
  page.profileId = 'plan'
  await page.save()
  expect(api.put.mock.calls[0][1]).toMatchObject({
    defaultModelId: 'default', titleGenerationModelId: 'title',
    dreamingModelId: 'default', dreamingReasoningEffort: 'high',
    imageGenerationModelId: 'images', imageEditingModelId: 'edits',
    profileModels: { plan: { modelId: 'profile', reasoningEffort: 'high' } }
  })

  useProviderStore().models.find((model) => model.id === 'profile').reasoningEfforts = []
  await nextTick()
  for (const label of ['Reasoning-Stufe', 'Agentmodell']) {
    const control = [...root.querySelectorAll('[role="combobox"]')].find((item) => item.closest('.q-field').textContent.includes(label))
    control.click()
    const automatic = await vi.waitUntil(() => document.querySelector('[role="option"]'))
    expect(automatic.querySelector('.q-item__label').textContent).toBe('Automatische Auswahl')
    automatic.click()
    await vi.waitFor(() => expect(document.querySelector('[role="listbox"]')).toBeNull())
  }
  await page.save()
  expect(api.put.mock.calls[1][1]).toMatchObject({ defaultModelId: 'default', titleGenerationModelId: 'title', profileModels: {} })
})

it('selects and persists a discovered speech voice and resets it to inheritance', async () => {
  const defaults = {
    configured: { speechGenerationModelId: 'speech', speechGenerationVoices: {}, profileModels: {} },
    inherited: { speechGenerationVoices: { speech: 'en-US-AvaMultilingualNeural' } },
    profiles: [], personalSettingsEnabled: true
  }
  const inventory = {
    providers: [{ id: 'azure', name: 'Azure', adapterType: 'azure-speech', enabled: true, hasCredential: true }],
    models: [{
      id: 'speech', providerId: 'azure', name: 'Azure Speech', enabled: true,
      capabilities: ['speech_generation'],
      discoveryMetadata: { speech_voices: ['de-DE-KatjaNeural', 'en-US-AvaMultilingualNeural'] }
    }]
  }
  api.get.mockImplementation(async (url) => ({ data: url === '/model-defaults' ? defaults : inventory }))
  api.put.mockImplementation(async (_url, configured) => ({ data: { ...defaults, configured: JSON.parse(JSON.stringify(configured)) } }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/models', component: Page }] })
  await router.push('/models')
  root = document.createElement('div')
  document.body.append(root)
  let page
  app = createApp({ render: () => h(RouterView, null, { default: ({ Component }) => h(Component, { ref: (instance) => { page = instance?.$?.setupState } }) }) })
  app.use(createPinia()).use(router).use(Quasar, quasarOptions)
  app.config.globalProperties.$q.notify = vi.fn()
  app.mount(root)
  await vi.waitFor(() => expect(page?.ready).toBe(true))

  const voiceField = () => [...root.querySelectorAll('.q-field')].find((field) => field.textContent.includes('Standardstimme'))
  expect(voiceField().textContent).toContain('Effektiv: en-US-AvaMultilingualNeural')
  voiceField().querySelector('[role="combobox"]').click()
  const katja = await vi.waitUntil(() => [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent.includes('de-DE-KatjaNeural')))
  katja.click()
  await nextTick()
  await page.save()
  expect(api.put.mock.calls[0][1].speechGenerationVoices).toEqual({ speech: 'de-DE-KatjaNeural' })
  expect(voiceField().textContent).toContain('de-DE-KatjaNeural')
  page.setSpeechVoice(null)
  await page.save()
  expect(api.put.mock.calls[1][1].speechGenerationVoices).toEqual({})
  expect(voiceField().textContent).toContain('Effektiv: en-US-AvaMultilingualNeural')
})
