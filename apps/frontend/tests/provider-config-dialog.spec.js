// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  Quasar, Notify, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection,
  QCheckbox, QChip, QDialog, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection,
  QSelect, QSeparator, QSpace, QToggle
} from 'quasar'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: {} }))

import ProviderConfigDialog from '../src/features/providers/ProviderConfigDialog.vue'
import ModelConfigDialog from '../src/features/providers/ModelConfigDialog.vue'
import { useProviderStore } from '../src/stores/providers'

let app, root

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})

afterEach(() => {
  app?.unmount()
  root?.remove()
  vi.restoreAllMocks()
})

async function mountDialog(component = ProviderConfigDialog, extraProps = {}) {
  const state = reactive({ modelValue: false, ...extraProps })
  const pinia = createPinia()
  const store = useProviderStore(pinia)
  const createProvider = vi.spyOn(store, 'createProvider').mockResolvedValue('azure')
  const updateProvider = vi.spyOn(store, 'updateProvider').mockResolvedValue()
  const updateModel = vi.spyOn(store, 'updateModel').mockResolvedValue()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(component, state) })
  app.use(pinia).use(Quasar, { plugins: { Notify }, components: {
    QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection, QCheckbox, QChip,
    QDialog, QExpansionItem, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection,
    QSelect, QSeparator, QSpace, QToggle
  } })
  app.mount(root)
  state.modelValue = true
  await vi.waitFor(() => expect(document.querySelector('.provider-dialog')).not.toBeNull())
  return { createProvider, updateProvider, updateModel }
}

function field(label) {
  return [...document.querySelectorAll('.provider-dialog .q-field')]
    .find((element) => element.querySelector('.q-field__label')?.textContent === label)
}

async function fill(label, value) {
  const input = field(label).querySelector('input')
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

function save(label = 'Provider hinzufügen') {
  const button = [...document.querySelectorAll('.provider-dialog button')]
    .find((element) => element.textContent.includes(label))
  button.click()
}

async function selectProvider(label) {
  field('Provider').querySelector('.q-field__native').click()
  await vi.waitFor(() => expect([...document.querySelectorAll('.q-item__label')]
    .some((element) => element.textContent === label)).toBe(true))
  const option = [...document.querySelectorAll('.q-item__label')]
    .find((element) => element.textContent === label)
  option.closest('.q-item').click()
  await vi.waitFor(() => expect(field('Name')).toBeDefined())
}

it('creates the Kimi subscription preset through the existing OpenAI-compatible endpoint', async () => {
  const { createProvider } = await mountDialog()
  await selectProvider('Kimi Code (Abonnement)')
  await fill('API-Key (optional)', 'kimi-test-key')
  save()
  await vi.waitFor(() => expect(createProvider).toHaveBeenCalledWith('openai-compatible', {
    name: 'Kimi Code', base_url: 'https://api.kimi.ai/coding/v1', api_type: 'responses',
    api_key: 'kimi-test-key', remove_api_key: false, trust_level: 'public',
    pseudonymization_enabled: true, memory_policy: 'inherit'
  }))
})

it('recognizes a saved Kimi connection without replacing its protocol, name, or credential', async () => {
  const { updateProvider } = await mountDialog(ProviderConfigDialog, { provider: {
    id: 'kimi', name: 'Mein Kimi', adapterType: 'openai-compatible', hasCredential: true,
    trustLevel: 'public', pseudonymizationEnabled: true,
    config: { baseUrl: 'https://api.kimi.com/coding/v1', apiType: 'chat_completions' }
  } })
  expect(field('Provider').textContent).toContain('Kimi Code (Abonnement)')
  save('Änderungen speichern')
  await vi.waitFor(() => expect(updateProvider).toHaveBeenCalledWith('kimi', {
    name: 'Mein Kimi', base_url: 'https://api.kimi.com/coding/v1', api_type: 'chat_completions',
    api_key: null, remove_api_key: false, trust_level: 'public',
    pseudonymization_enabled: true, memory_policy: 'inherit'
  }))
})

it.each([
  ['Azure', 'azure-openai-realtime', 'https://my-resource.openai.azure.com'],
  ['OpenAI', 'openai-realtime', 'https://api.openai.com/v1']
])('creates %s through the shared Realtime provider form', async (service, adapterType, endpoint) => {
  const { createProvider } = await mountDialog()
  await selectProvider('Realtime')
  field('Dienst').querySelector('.q-field__native').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('.q-item__label')]
    .find((element) => element.textContent === service))
  option.closest('.q-item').click()
  await nextTick()
  await fill('Name', 'Meine Sprachverbindung')
  await fill('Base URL', endpoint)
  await fill('API-Key (optional)', 'test-key')
  save()
  await vi.waitFor(() => expect(createProvider).toHaveBeenCalledWith(adapterType, {
    name: 'Meine Sprachverbindung', base_url: endpoint, api_key: 'test-key',
    remove_api_key: false, trust_level: 'public', pseudonymization_enabled: true,
    memory_policy: 'inherit'
  }))
})

it.each([
  ['Azure', 'azure-openai-realtime', 'https://my-resource.openai.azure.com/openai/v1'],
  ['OpenAI', 'openai-realtime', 'https://api.openai.com/v1']
])('preserves a saved %s connection in the shared Realtime form', async (service, adapterType, endpoint) => {
  const { updateProvider } = await mountDialog(ProviderConfigDialog, { provider: {
    id: 'voice', name: 'Meine Sprachverbindung', adapterType, hasCredential: true,
    trustLevel: 'private', pseudonymizationEnabled: false, memoryPolicy: 'deny',
    config: { baseUrl: endpoint }
  } })
  expect(field('Provider').textContent).toContain('Realtime')
  expect(field('Dienst').textContent).toContain(service)
  expect(field('Dienst').classList.contains('q-field--disabled')).toBe(true)
  save('Änderungen speichern')
  await vi.waitFor(() => expect(updateProvider).toHaveBeenCalledWith('voice', {
    name: 'Meine Sprachverbindung', base_url: endpoint, api_key: null,
    remove_api_key: false, trust_level: 'private', pseudonymization_enabled: false,
    memory_policy: 'deny'
  }))
})

it.each([
  'https://my-speech.cognitiveservices.azure.com',
  'https://westeurope.api.cognitive.microsoft.com'
])('creates Azure Speech with its HTTPS resource endpoint %s and public defaults', async (endpoint) => {
  const { createProvider } = await mountDialog()
  await selectProvider('Azure Speech')
  expect(field('API-Protokoll')).toBeUndefined()
  expect(document.querySelector('.provider-dialog').textContent).toContain('Das Originalaudio wird ohne Pseudonymisierung an Azure gesendet')
  expect(document.querySelector('.provider-dialog').textContent).toContain('Keine Live-Transkription')
  await fill('Name', 'Azure Speech')
  await fill('Azure-Speech-Endpunkt', endpoint)
  await fill('Azure-Speech-API-Key', 'test-key')
  save()
  await vi.waitFor(() => expect(createProvider).toHaveBeenCalledWith('azure-speech', {
    name: 'Azure Speech', base_url: endpoint, api_key: 'test-key', remove_api_key: false,
    trust_level: 'public', pseudonymization_enabled: true, memory_policy: 'inherit'
  }))
})

it('requires a new key and rejects non-HTTPS or non-origin Azure endpoints', async () => {
  const { createProvider } = await mountDialog()
  await selectProvider('Azure Speech')
  await fill('Name', 'Azure Speech')
  await fill('Azure-Speech-Endpunkt', 'https://my-speech.cognitiveservices.azure.com')
  save()
  await vi.waitFor(() => expect(field('Azure-Speech-API-Key').textContent).toContain('Pflichtfeld'))
  expect(createProvider).not.toHaveBeenCalled()
  await fill('Azure-Speech-API-Key', 'test-key')
  for (const endpoint of [
    'http://my-speech.cognitiveservices.azure.com',
    'https://my-speech.cognitiveservices.azure.com/speechtotext/transcriptions:transcribe',
    'https://my-speech.cognitiveservices.azure.com?api-version=2025-10-15',
    'https://my-speech.cognitiveservices.azure.com#fragment',
    'https://user:password@my-speech.cognitiveservices.azure.com'
  ]) {
    await fill('Azure-Speech-Endpunkt', endpoint)
    save()
    await vi.waitFor(() => expect(field('Azure-Speech-Endpunkt').textContent)
      .toContain('Bitte gib einen HTTPS-Endpunkt'))
    expect(createProvider).not.toHaveBeenCalled()
  }
})

it.each([false, true])('retains or explicitly removes a saved Azure credential (remove=%s)', async (removeKey) => {
  const { updateProvider } = await mountDialog(ProviderConfigDialog, { provider: {
    id: 'azure', name: 'Azure Speech', adapterType: 'azure-speech', hasCredential: true,
    trustLevel: 'public', pseudonymizationEnabled: true, memoryPolicy: 'inherit',
    config: { base_url: 'https://westeurope.api.cognitive.microsoft.com' }
  } })
  expect(field('Azure-Speech-Endpunkt').querySelector('input').value)
    .toBe('https://westeurope.api.cognitive.microsoft.com')
  expect(field('API-Protokoll')).toBeUndefined()
  expect(field('Neuer API-Key (optional)').textContent).toContain('Leer lassen')
  if (removeKey) {
    await fill('Neuer API-Key (optional)', 'replacement-key')
    document.querySelector('.provider-dialog .q-checkbox').click()
    await nextTick()
    expect(document.querySelector('.provider-dialog').textContent).toContain('Ohne API-Key sind weder Transkription noch Spracherzeugung möglich.')
  }
  save('Änderungen speichern')
  await vi.waitFor(() => expect(updateProvider).toHaveBeenCalledWith('azure', {
    name: 'Azure Speech', base_url: 'https://westeurope.api.cognitive.microsoft.com',
    api_key: null, remove_api_key: removeKey, trust_level: 'public',
    pseudonymization_enabled: true, memory_policy: 'inherit'
  }))
})

it('saves manually selected capabilities for a discovered OpenAI-compatible Azure model', async () => {
  const { updateModel } = await mountDialog(ModelConfigDialog, {
    provider: { id: 'azure', name: 'Azure OpenAI', adapterType: 'openai-compatible' },
    model: {
      id: 'chat', modelId: 'azure-chat', origin: 'discovered', enabled: true,
      capabilities: []
    }
  })
  field('Modellfähigkeiten').querySelector('.q-field__native').click()
  const option = await vi.waitUntil(() => [...document.querySelectorAll('.q-item__label')]
    .find((element) => element.textContent === 'Chat'))
  option.closest('.q-item').click()
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }))
  await nextTick()
  save('Änderungen speichern')
  await vi.waitFor(() => expect(updateModel).toHaveBeenCalledWith(
    'chat', expect.objectContaining({ capabilities: ['chat'] })
  ))
})

it('keeps the discovered Azure model transcription-only when editing its display name', async () => {
  const { updateModel } = await mountDialog(ModelConfigDialog, {
    provider: { id: 'azure', name: 'Azure Speech', adapterType: 'azure-speech' },
    model: {
      id: 'transcription', modelId: 'azure-speech-fast-transcription',
      name: 'Fast Transcription', origin: 'discovered', enabled: true,
      capabilities: ['audio_transcription'], inputModalities: ['audio'],
      supportsTools: false, supportsParallelToolCalls: false, supportsStreaming: false
    }
  })
  expect(field('Modell-ID').querySelector('input').readOnly).toBe(true)
  expect(field('Modellfähigkeiten').classList.contains('q-field--readonly')).toBe(true)
  expect(document.querySelector('.provider-dialog').textContent).toContain('verbindlich vor')
  expect(document.querySelector('.provider-dialog').textContent).toContain('Die LLM-Laufzeitfelder gelten nur für Chatmodelle.')
  await fill('Anzeigename (optional)', 'Meine Transkription')
  save('Änderungen speichern')
  await vi.waitFor(() => expect(updateModel).toHaveBeenCalled())
  expect(updateModel.mock.calls[0][1]).toMatchObject({
    name: 'Meine Transkription', supports_tools: false,
    supports_parallel_tool_calls: false, supports_streaming: false
  })
  expect(updateModel.mock.calls[0][1]).not.toHaveProperty('capabilities')
})
