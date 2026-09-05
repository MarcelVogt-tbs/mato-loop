// @vitest-environment jsdom
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ notify: vi.fn(), api: { post: vi.fn(), get: vi.fn(), delete: vi.fn() }, recorder: null }))
vi.mock('src/boot/axios', () => ({ api: mocks.api }))
vi.mock('quasar', () => ({ useQuasar: () => ({ notify: mocks.notify, screen: { gt: { sm: true } } }) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: async () => true }) }))
vi.mock('src/composables/audio-recorder', () => ({
  useAudioRecorder: (options) => { mocks.onAutoStopped = options.onAutoStopped; return mocks.recorder },
  recorderErrorMessage: () => 'Aufnahme fehlgeschlagen'
}))
vi.mock('components/SessionSettingsFields.vue', () => ({ default: { render: () => null } }))
vi.mock('components/ApprovalCard.vue', () => ({ default: { render: () => null } }))
vi.mock('components/AgentActivityPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('components/AppLogo.vue', () => ({ default: { render: () => null } }))
vi.mock('components/ChatQueuedMessages.vue', () => ({ default: { render: () => null } }))
vi.mock('components/ControlCenterPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('components/MessageContent.vue', () => ({ default: { render: () => null } }))
vi.mock('components/MessageAttachments.vue', () => ({ default: { render: () => null } }))
vi.mock('components/QuestionDock.vue', () => ({ default: { render: () => null } }))

import ChatComposer from '../src/components/ChatComposer.vue'
import ChatPage from '../src/pages/ChatPage.vue'
import { useChatStore } from '../src/stores/chat'
import { useProviderStore } from '../src/stores/providers'
import { useToolSettingsStore } from '../src/stores/tool-settings'
import { useInstanceLimitsStore } from '../src/stores/instance-limits'

let app, root, chat, session, stopResolve, showPage, router
const file = (name = 'aufnahme.wav') => new File(['wav'], name, { type: 'audio/wav' })

async function click(label) {
  const button = [...root.querySelectorAll('button')].find((item) => (
    item.getAttribute('aria-label') === label || item.textContent === label
  ))
  expect(button, label).toBeTruthy()
  button.click()
  await nextTick()
}

beforeEach(async () => {
  setActivePinia(createPinia())
  chat = useChatStore()
  chat.draftOwnerUserId = 'owner'
  chat.current = { id: 'session-1' }
  session = ref(chat.current)
  const providers = useProviderStore()
  providers.audioTranscriptionModelId = 'audio'
  vi.spyOn(providers, 'capabilityOptions', 'get').mockReturnValue(() => [{ value: 'audio' }])
  useToolSettingsStore().loaded = true
  mocks.recorder = {
    starting: ref(false), recording: ref(false), finalizing: ref(false),
    durationMs: ref(1000), limitReached: ref(false), autoStoppedFile: ref(null),
    startRecording: vi.fn(async () => { mocks.recorder.recording.value = true }),
    stopRecording: vi.fn(() => {
      mocks.recorder.recording.value = false
      mocks.recorder.finalizing.value = true
      return new Promise((resolve) => {
        stopResolve = (value) => { mocks.recorder.finalizing.value = false; resolve(value) }
      })
    }),
    cancelRecording: vi.fn(async () => {
      mocks.recorder.recording.value = false
      mocks.recorder.starting.value = false
    }),
    clearAutoStoppedFile: () => { mocks.recorder.autoStoppedFile.value = null }
  }
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:audio'), revokeObjectURL: vi.fn() })
  root = document.createElement('div')
  document.body.append(root)
  showPage = ref(false)
  router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'chat', component: ChatPage }] })
  await router.push('/?session=session-1')
  app = createApp({
    render: () => showPage.value ? h(ChatPage) : h(ChatComposer, {
      session: session.value,
      attachments: chat.activeAttachmentDraft,
      'onAdd-attachments': (files, done, options) => done?.(chat.addAttachmentFiles(files, options)),
      'onRemove-attachment': (id) => chat.removeAttachmentFile(id)
    })
  })
  app.use(router)
  app.config.globalProperties.$q = { screen: { lt: { sm: false }, gt: { xs: true } } }
  app.component('QBtn', defineComponent({
    props: ['label', 'disable', 'loading'],
    setup: (props) => () => h('button', { disabled: props.disable || props.loading }, props.label)
  }))
  for (const name of ['QCard', 'QForm', 'QInput', 'QIcon', 'QSpace', 'QBanner', 'QTooltip', 'QSpinner', 'QChip', 'QPopupProxy', 'QToolbar', 'QToolbarTitle', 'QSeparator', 'QCardSection']) {
    app.component(name, { methods: { focus() {} }, render() { return h('div', this.$slots.default?.()) } })
  }
  app.directive('close-popup', {})
  app.component('QPage', { render() { return h('div', this.$slots.default?.()) } })
  app.component('QSplitter', { render() { return h('div', this.$slots.before?.()) } })
  app.component('QScrollArea', { methods: { getScrollTarget() { return null } }, render() { return h('div', this.$slots.default?.()) } })
  app.component('QVirtualScroll', { render: () => null })
  app.component('QSkeleton', { render: () => null })
  app.mount(root)
})

afterEach(() => {
  app?.unmount()
  chat.cancelQueueDrain()
  chat.clearAllAttachmentDrafts()
  root?.remove()
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

describe('voice attachment composer', () => {
  it.each(['model', 'disabled', 'loading', 'failed'])('records with unavailable transcription: %s', async (reason) => {
    const settings = useToolSettingsStore()
    if (reason === 'model') useProviderStore().audioTranscriptionModelId = null
    if (reason === 'disabled') settings.transcribeAudioAvailable = false
    if (reason === 'loading') settings.loading = true
    if (reason === 'failed') settings.loadFailed = true
    await click('Sprachnachricht aufnehmen')
    expect(mocks.recorder.startRecording).toHaveBeenCalledOnce()
  })

  it.each(['recording', 'starting', 'finalizing'])('hands off actual route navigation while %s', async (state) => {
    chat.outboxLoaded = true
    mocks.api.get.mockImplementation(async (url) => ({ data: url.startsWith('/sessions/session-')
      ? { id: url.split('/').pop(), messages: [] }
      : {} }))
    showPage.value = true
    await vi.waitFor(() => expect(mocks.api.get.mock.calls.some(([url]) => url === '/sessions/session-1')).toBe(true))
    await vi.waitFor(() => expect(chat.current?.id).toBe('session-1'))
    await nextTick()
    await click('Sprachnachricht aufnehmen')
    if (state === 'starting') {
      mocks.recorder.recording.value = false
      mocks.recorder.starting.value = true
    }
    if (state === 'finalizing') await click('Als Sprachnachricht anhängen')
    mocks.api.get.mockClear()
    await router.push('/?session=session-2')
    if (state !== 'starting') {
      expect(mocks.recorder.stopRecording).toHaveBeenCalledOnce()
      expect(chat.current.id).toBe('session-1')
      expect(mocks.api.get.mock.calls.some(([url]) => url === '/sessions/session-2')).toBe(false)
      stopResolve(file())
    }
    await vi.waitFor(() => expect(chat.current?.id).toBe('session-2'))
    expect(chat.activeAttachmentDraft).toEqual([])
    if (state === 'starting') {
      expect(mocks.recorder.cancelRecording).toHaveBeenCalled()
      expect(chat.attachmentDrafts.sessionMessages['session-1']).toBeUndefined()
    } else {
      expect(chat.attachmentDrafts.sessionMessages['session-1'][0].voiceMessage).toBe(true)
    }
  })

  it.each(['delete', 'logout', 'cancel'])('does not retain a late recording after explicit %s', async (action) => {
    await click('Sprachnachricht aufnehmen')
    if (action === 'cancel') await click('Abbrechen')
    else {
      await click('Als Sprachnachricht anhängen')
      if (action === 'delete') {
        mocks.api.delete.mockResolvedValue({})
        await chat.deleteSession('session-1')
      } else chat.setDraftOwner(null)
      stopResolve(file())
    }
    mocks.onAutoStopped(file('late.wav'))
    await nextTick()
    expect(chat.getAudioDictation('session-1')).toBeNull()
    expect(chat.attachmentDrafts.sessionMessages['session-1']).toBeUndefined()
  })

  it('retains multiple recordings as marked, playable attachments without draft transcription', async () => {
    for (const name of ['eins.wav', 'zwei.wav']) {
      await click('Sprachnachricht aufnehmen')
      await click('Als Sprachnachricht anhängen')
      stopResolve(file(name))
      await nextTick()
      await nextTick()
    }
    expect(chat.activeAttachmentDraft.map((item) => item.voiceMessage)).toEqual([true, true])
    expect(root.querySelectorAll('audio[controls]')).toHaveLength(2)
    expect(root.textContent).toContain('Transkription beim Senden')
    expect(mocks.api.post).not.toHaveBeenCalled()
    await click('eins.wav entfernen')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:audio')
  })

  it('keeps rejected recordings for download and retry without overwriting earlier recordings', async () => {
    useInstanceLimitsStore().maxAttachmentsPerMessage = 0
    for (const name of ['eins.wav', 'zwei.wav']) {
      await click('Sprachnachricht aufnehmen')
      await click('Als Sprachnachricht anhängen')
      stopResolve(file(name))
      await nextTick()
      await nextTick()
    }
    expect(chat.getAudioDictation('session-1').files).toHaveLength(2)
    const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await click('Herunterladen')
    expect(download).toHaveBeenCalledOnce()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:audio')
    useInstanceLimitsStore().maxAttachmentsPerMessage = 8
    await click('Anhängen')
    expect(chat.activeAttachmentDraft[0].voiceMessage).toBe(true)
    expect(chat.getAudioDictation('session-1').files.map((item) => item.name)).toEqual(['zwei.wav'])
    download.mockRestore()
  })

  it.each(['session', 'project'])('binds an asynchronous stop to the original %s scope', async (scope) => {
    if (scope === 'project') {
      chat.current = null
      session.value = {}
      chat.sessionsProjectId = 'project-1'
      await nextTick()
    }
    await click('Sprachnachricht aufnehmen')
    await click('Als Sprachnachricht anhängen')
    if (scope === 'project') chat.sessionsProjectId = 'project-2'
    else {
      chat.current = { id: 'session-2' }
      session.value = chat.current
    }
    await nextTick()
    stopResolve(file())
    await nextTick()
    await nextTick()
    const attachments = scope === 'project'
      ? chat.attachmentDrafts.newSessions['project-1']
      : chat.attachmentDrafts.sessionMessages['session-1']
    expect(attachments[0].voiceMessage).toBe(true)
    expect(chat.activeAttachmentDraft).toEqual([])
  })

  it('attaches automatically stopped recordings and preserves late stops after unmount', async () => {
    await click('Sprachnachricht aufnehmen')
    mocks.recorder.recording.value = false
    mocks.onAutoStopped(file('auto.wav'))
    await nextTick()
    expect(chat.activeAttachmentDraft[0].voiceMessage).toBe(true)
    await click('Sprachnachricht aufnehmen')
    await click('Als Sprachnachricht anhängen')
    app.unmount()
    stopResolve(file('late.wav'))
    await nextTick()
    expect(chat.getAudioDictation('session-1').files[0].name).toBe('late.wav')
    mocks.onAutoStopped(file('late-auto.wav'))
    expect(chat.getAudioDictation('session-1').files.map((item) => item.name)).toEqual(['late.wav', 'late-auto.wav'])
  })
})
