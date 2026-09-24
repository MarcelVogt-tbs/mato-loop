// @vitest-environment jsdom
import { createApp, h, nextTick, reactive } from 'vue'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  Quasar, Notify, QAvatar, QBanner, QBtn, QCard, QCardActions, QCardSection,
  QCheckbox, QChip, QDialog, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection,
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
    QDialog, QForm, QIcon, QInput, QItem, QItemLabel, QItemSection,
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

async function selectAzure() {
  field('Provider').querySelector('.q-field__native').click()
  await vi.waitFor(() => expect([...document.querySelectorAll('.q-item__label')]
    .some((element) => element.textContent === 'Azure Speech')).toBe(true))
  const option = [...document.querySelectorAll('.q-item__label')]
    .find((element) => element.textContent === 'Azure Speech')
  option.closest('.q-item').click()
  await vi.waitFor(() => expect(field('Azure-Speech-Endpunkt')).toBeDefined())
}

it.each([
  'https://my-speech.cognitiveservices.azure.com',
  'https://westeurope.api.cognitive.microsoft.com'
])('creates Azure Speech with its HTTPS resource endpoint %s and public defaults', async (endpoint) => {
  const { createProvider } = await mountDialog()
  await selectAzure()
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
  await selectAzure()
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
    expect(document.querySelector('.provider-dialog').textContent).toContain('Ohne API-Key ist keine Transkription möglich.')
  }
  save('Änderungen speichern')
  await vi.waitFor(() => expect(updateProvider).toHaveBeenCalledWith('azure', {
    name: 'Azure Speech', base_url: 'https://westeurope.api.cognitive.microsoft.com',
    api_key: null, remove_api_key: removeKey, trust_level: 'public',
    pseudonymization_enabled: true, memory_policy: 'inherit'
  }))
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
