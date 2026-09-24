// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { copyToClipboard } from 'quasar'
import FileViewerApp from '../src/features/files/components/FileViewerApp.vue'
import FileViewerPanel from '../src/features/files/components/FileViewerPanel.vue'
import { useControlCenterStore } from '../src/stores/control-center'
import { useWorkspaceStore } from '../src/stores/workspace'

const mocks = vi.hoisted(() => ({ confirm: vi.fn(), api: { get: vi.fn(), put: vi.fn() } }))

vi.mock('src/boot/axios', () => ({ api: mocks.api }))
vi.mock('quasar', () => ({
  copyToClipboard: vi.fn(),
  useQuasar: () => ({ screen: { gt: { xs: true }, lt: { sm: false } }, notify: vi.fn() })
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ resolve: () => ({ href: '/print' }) }) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: mocks.confirm }) }))
vi.mock('src/features/chat/components/MessageContent.vue', () => ({ default: {
  props: ['content'], render() { return h('article', this.content) }
} }))
vi.mock('src/features/files/components/StlViewerPanel.vue', () => ({ default: {
  props: ['src'], render() { return h('div', { 'data-model-src': this.src }) }
} }))
vi.mock('src/features/files/components/WorkspaceFileEditor.vue', () => ({ __esModule: true, default: {
  props: ['modelValue', 'filename', 'markdown', 'saving', 'dirty', 'session', 'showToolbar'],
  emits: ['update:modelValue', 'save', 'cancel'],
  render() {
    return h('div', [
      this.showToolbar !== false ? h('div', { role: 'toolbar', 'data-editor-toolbar': '' }, [
        h('button', { disabled: !this.dirty || this.saving, onClick: () => this.$emit('save') }, 'Speichern'),
        h('button', { disabled: !this.dirty || this.saving, onClick: () => this.$emit('cancel') }, 'Änderungen verwerfen'),
        h('div', { 'data-file-menu': '' }, this.$slots['file-menu']?.())
      ]) : null,
      this.showToolbar !== false ? h('hr', { role: 'separator' }) : null,
      h('div', { 'data-before-content': '' }, this.$slots['before-content']?.()),
      h('textarea', {
        'aria-label': this.markdown ? 'Markdown-Quelltext' : 'Textinhalt',
        value: this.modelValue,
        onInput: (event) => this.$emit('update:modelValue', event.target.value)
      })
    ])
  }
} }))
vi.mock('src/features/files/components/ImageViewerPanel.vue', () => ({ default: { render: () => null } }))

let app
beforeEach(() => { mocks.confirm.mockReset(); mocks.api.get.mockReset(); mocks.api.put.mockReset(); copyToClipboard.mockReset() })
afterEach(() => { app?.unmount(); vi.restoreAllMocks() })

it.each(['text', 'markdown', 'image', 'stl', 'openscad'])(
  'uses the normal %s viewer read-only for a historical revision without rendering dependencies', async (kind) => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const workspace = useWorkspaceStore()
    workspace.sessionId = 'session-id'
    workspace.rootIdentity = 'root-id'
    const entry = { kind: 'file', path: 'model.scad', name: 'model.scad', fileId: 'file-id', revision: 1 }
    workspace.viewedEntry = entry
    const preview = vi.spyOn(workspace, 'revisionPreview').mockResolvedValue({ kind, content: 'old source' })
    const livePreview = vi.spyOn(workspace, 'preview')
    const render = vi.spyOn(workspace, 'renderOpenScad')
    const root = document.createElement('div')
    app = createApp({ render: () => h(FileViewerPanel, { entry }) })
    app.use(pinia)
    app.config.globalProperties.$q = { screen: { gt: { xs: true }, lt: { sm: false } } }
    for (const name of ['QToolbar', 'QToolbarTitle', 'QBtn', 'QBtnToggle', 'QTooltip', 'QSeparator', 'QBanner', 'QSpinner', 'QIcon', 'QCard', 'QCardSection', 'QCardActions', 'QItem', 'QItemSection', 'QItemLabel', 'QChip', 'QList']) {
      app.component(name, { render() { return h('div', { ...this.$attrs, role: name === 'QToolbar' ? 'toolbar' : undefined }, this.$slots.default?.()) } })
    }
    app.component('QDialog', { render: () => null })
    app.directive('close-popup', {})
    app.directive('ripple', {})
    app.mount(root)
    for (let tick = 0; tick < 5; tick++) await nextTick()
    expect(preview).toHaveBeenCalledWith(entry.path, 1, 'file-id')
    expect(livePreview).not.toHaveBeenCalled()
    expect(render).not.toHaveBeenCalled()
    expect(root.textContent).toContain('Schreibgeschützt')
    expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
    expect(root.querySelector('.file-viewer__toolbar')).not.toBeNull()
    expect(root.querySelector('section').getAttribute('aria-label')).toBe('model.scad · Version 1')
    expect(root.querySelector('[aria-label="Datei bearbeiten"]')).toBeNull()
    expect(root.querySelector('[aria-label="STL erzeugen und herunterladen"]')).toBeNull()
    if (kind === 'openscad') expect(root.textContent).toContain('nur den Quelltext')
    if (kind === 'markdown') expect(root.querySelector('article').textContent).toBe('old source')
    if (['text', 'openscad'].includes(kind)) expect(root.querySelector('pre').textContent).toBe('old source')
    if (kind === 'image') expect(root.querySelector('img').getAttribute('src')).toContain('/revisions/1/content?')
    if (kind === 'stl') expect(root.querySelector('[data-model-src]').dataset.modelSrc).toContain('file_id=file-id')
  }
)

it('activates a historical revision only after confirmation', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workspace = useWorkspaceStore()
  workspace.sessionId = 'session-id'
  workspace.scope = 'session'
  workspace.rootIdentity = 'root-id'
  const entry = { kind: 'file', path: 'note.txt', name: 'note.txt' }
  workspace.viewedEntry = entry
  vi.spyOn(workspace, 'preview').mockResolvedValue({ kind: 'text', content: 'current' })
  const state = {
    fileId: 'file-id',
    items: [
      { revision: 2, current: true, sha256: 'b'.repeat(64), createdAt: new Date().toISOString() },
      { revision: 1, current: false, sha256: 'a'.repeat(64), createdAt: new Date().toISOString() }
    ]
  }
  vi.spyOn(workspace, 'loadRevisions').mockImplementation(async (path) => {
    workspace.fileRevisions[path] = state
    return state
  })
  const activate = vi.spyOn(workspace, 'activateRevision').mockResolvedValue({ currentRevision: 1 })
  mocks.confirm.mockResolvedValue(true)
  const root = document.createElement('div')
  app = createApp({ render: () => h(FileViewerPanel, { entry }) })
  app.use(pinia)
  app.config.globalProperties.$q = { screen: { gt: { xs: true }, lt: { sm: false } } }
  for (const name of ['QToolbar', 'QToolbarTitle', 'QBtnToggle', 'QTooltip', 'QSeparator', 'QBanner', 'QSpinner', 'QIcon', 'QCard', 'QCardSection', 'QCardActions', 'QItem', 'QItemSection', 'QItemLabel', 'QChip', 'QList', 'QDialog']) {
    app.component(name, { render() { return h('div', this.$attrs, this.$slots.default?.()) } })
  }
  app.component('QBtn', { render() { return h('button', this.$attrs, this.$slots.default?.()) } })
  app.directive('close-popup', {})
  app.directive('ripple', {})
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('textarea')).not.toBeNull())

  root.querySelector('[aria-label="Versionsverlauf öffnen"]').click()
  for (let tick = 0; tick < 3; tick++) await nextTick()
  root.querySelector('[aria-label="Version 1 aktivieren"]').click()
  for (let tick = 0; tick < 3; tick++) await nextTick()

  expect(mocks.confirm).toHaveBeenCalledWith(
    'Soll Version 1 aktiviert werden? Die Datei wird auf diesen Stand gesetzt. Alle Versionen bleiben erhalten.',
    'Version aktivieren?',
    { okLabel: 'Aktivieren', okColor: 'primary' }
  )
  expect(activate).toHaveBeenCalledWith('note.txt', 1, state)
})

async function mountLiveViewer({ kind = 'text', path = 'note.txt', toolContext = null, toolbar, viaApp = false, fullscreenToggle = false, previewError = null } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const workspace = useWorkspaceStore()
  useControlCenterStore().sessionId = 'session-id'
  workspace.configure({ sessionId: 'session-id', rootIdentity: 'root-id' })
  workspace.toolContext = toolContext
  if (toolContext) workspace.scope = 'filesystem'
  workspace.openEntry({ kind: 'file', path, name: path.split('/').at(-1) })
  if (previewError) mocks.api.get.mockRejectedValue(previewError)
  else mocks.api.get.mockResolvedValue({ data: { kind, content: 'original', revision: 'a'.repeat(64) } })
  const preview = vi.spyOn(workspace, 'preview')
  const saveFile = vi.spyOn(workspace, 'saveFile')
  const render = vi.spyOn(workspace, 'renderOpenScad').mockResolvedValue(null)
  vi.spyOn(workspace, 'refreshStorageUsage').mockResolvedValue(null)
  const root = document.createElement('div')
  const toggleFullscreen = vi.fn()
  const tab = { tabId: 'file-a', kind: 'file', fileTarget: workspace.fileTarget(workspace.viewedEntry) }
  app = createApp({ render: () => h(
    viaApp ? FileViewerApp : FileViewerPanel,
    viaApp ? { tab } : { entry: workspace.viewedEntry, fullscreenToggle, onToggleFullscreen: toggleFullscreen },
    toolbar ? { toolbar } : undefined
  ) })
  app.use(pinia)
  app.config.globalProperties.$q = { screen: { gt: { xs: true }, lt: { sm: false } } }
  for (const name of ['QToolbar', 'QToolbarTitle', 'QTooltip', 'QSeparator', 'QBanner', 'QSpinner', 'QIcon', 'QCard', 'QCardSection', 'QCardActions', 'QItemSection', 'QItemLabel', 'QChip', 'QList']) {
    app.component(name, { render() { return h('div', {
      ...this.$attrs, ...(name === 'QToolbar' ? { role: 'toolbar' } : name === 'QSeparator' ? { role: 'separator' } : {})
    }, [this.$slots.default?.(), this.$slots.action?.()]) } })
  }
  app.component('QItem', {
    props: ['disable', 'clickable'],
    render() { return h('button', { ...this.$attrs, disabled: this.disable, 'aria-disabled': Boolean(this.disable) }, this.$slots.default?.()) }
  })
  app.component('QBtn', {
    props: ['label', 'disable', 'loading'],
    render() { return h('button', { ...this.$attrs, disabled: this.disable || this.loading }, [this.label, this.$slots.default?.()]) }
  })
  app.component('QBtnToggle', {
    props: ['modelValue', 'options'],
    emits: ['update:modelValue'],
    render() {
      return h('div', this.options.map((option) => h('button', {
        ...option.attrs, disabled: option.disable,
        onClick: () => this.$emit('update:modelValue', option.value)
      }, option.label)))
    }
  })
  app.component('QDialog', {
    props: ['modelValue'],
    render() { return this.modelValue ? h('div', this.$slots.default?.()) : null }
  })
  app.directive('close-popup', {})
  app.directive('ripple', {})
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('textarea, pre, article, img, [data-model-src], [aria-label="OpenSCAD-Ansicht wählen"], [role="alert"]')).not.toBeNull())
  return { root, workspace, preview, saveFile, render, toggleFullscreen }
}

function button(root, label) {
  const element = [...root.querySelectorAll('button')].find((item) =>
    item.textContent.trim() === label || item.getAttribute('aria-label') === label
  )
  expect(element, label).toBeTruthy()
  return element
}

it('explains that a historical workspace output can have been moved or deleted', async () => {
  const error = { response: { status: 404, data: { error: { code: 'file.pathNotFound' } } } }
  const { root } = await mountLiveViewer({
    path: '/project/_probe2.py',
    toolContext: { executionAgentId: 'historical-agent', currentDirectory: '/project' },
    previewError: error
  })

  expect(root.textContent).toContain('Datei nicht gefunden')
  expect(root.textContent).toContain('Unter „/project/_probe2.py“ ist keine Datei mehr verfügbar.')
  expect(root.textContent).toContain('Sie wurde möglicherweise verschoben oder gelöscht.')
  expect(button(root, 'Erneut versuchen')).toBeTruthy()
})

async function type(root, content) {
  const editor = root.querySelector('textarea')
  editor.value = content
  editor.dispatchEvent(new Event('input', { bubbles: true }))
  await nextTick()
}

async function rejectSave(root) {
  mocks.api.put.mockRejectedValueOnce({ response: { status: 409, data: { error: { code: 'file.changed' } } } })
  await type(root, 'my draft')
  button(root, 'Speichern').click()
  await vi.waitFor(() => expect(button(root, 'Änderungen vergleichen').disabled).toBe(false))
}

it.each(['text', 'markdown'])('opens writable %s in the editor by default and confirms discarding changes', async (kind) => {
  const { root, workspace } = await mountLiveViewer({ kind, path: kind === 'markdown' ? 'note.md' : 'note.txt' })
  expect(root.querySelector('textarea').value).toBe('original')
  expect(root.querySelector('textarea').getAttribute('aria-label')).toBe(kind === 'markdown' ? 'Markdown-Quelltext' : 'Textinhalt')
  expect(workspace.editorActive).toBe(true)
  expect(workspace.editorDirty).toBe(false)
  expect(button(root, 'Speichern').disabled).toBe(true)
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  expect(root.querySelector('[data-editor-toolbar]')).not.toBeNull()
  expect(root.querySelector('.file-viewer__toolbar')).toBeNull()
  expect(root.querySelectorAll('[role="separator"]')).toHaveLength(1)
  expect(root.querySelector('section').getAttribute('aria-label')).toBe(workspace.viewedEntry.name)
  expect(root.querySelector('[aria-label="Im Vollbild öffnen"]')).toBeNull()

  await type(root, 'my draft')
  expect(workspace.editorDraft).toBe('my draft')
  expect(workspace.editorDirty).toBe(true)
  mocks.confirm.mockResolvedValueOnce(false)
  button(root, 'Änderungen verwerfen').click()
  await nextTick()
  expect(mocks.confirm).toHaveBeenCalledWith(
    'Sollen die ungespeicherten Änderungen verworfen werden?', 'Änderungen verwerfen?', { okLabel: 'Verwerfen' }
  )
  expect(workspace.editorDraft).toBe('my draft')
  expect(workspace.editorDirty).toBe(true)

  mocks.confirm.mockResolvedValueOnce(true)
  button(root, 'Änderungen verwerfen').click()
  await vi.waitFor(() => expect(root.querySelector('textarea').value).toBe('original'))
  expect(workspace.editorActive).toBe(true)
  expect(workspace.editorDirty).toBe(false)
  expect(mocks.api.put).not.toHaveBeenCalled()
})

it('keeps typing during a real store save and uses the saved content as the new dirty baseline', async () => {
  const { root, workspace, saveFile } = await mountLiveViewer()
  let resolveSave
  mocks.api.put.mockImplementationOnce(() => new Promise((resolve) => { resolveSave = resolve }))
  await type(root, 'sent')
  const editor = root.querySelector('textarea')
  button(root, 'Speichern').click()
  await nextTick()
  expect(button(root, 'Speichern').disabled).toBe(true)
  expect(button(root, 'Versionsverlauf öffnen').disabled).toBe(true)
  expect(editor.disabled).toBe(false)
  await type(root, 'original')
  expect(workspace.editorDirty).toBe(false)
  expect(button(root, 'Versionsverlauf öffnen').disabled).toBe(true)
  await type(root, 'typed while saving')
  expect(mocks.api.put).toHaveBeenCalledWith('/workspace/files', {
    path: 'note.txt', content: 'sent', expected_revision: 'a'.repeat(64)
  }, {
    params: { scope: 'session', session_id: 'session-id', root_identity: 'root-id' },
    suppressErrorToast: true
  })
  resolveSave({ data: { revision: 'b'.repeat(64) } })
  await saveFile.mock.results[0].value
  await nextTick()

  expect(root.querySelector('textarea')).toBe(editor)
  expect(editor.value).toBe('typed while saving')
  expect(workspace.editorActive).toBe(true)
  expect(workspace.editorDirty).toBe(true)
  expect(button(root, 'Speichern').disabled).toBe(false)
  expect(workspace.filePreviews['note.txt']).toMatchObject({ content: 'sent', revision: 'b'.repeat(64) })
  await type(root, 'sent')
  expect(workspace.editorDirty).toBe(false)
  await type(root, 'original')
  expect(workspace.editorDirty).toBe(true)
})

it('can reload a known external change after undo returns the draft to its baseline', async () => {
  const { root, workspace } = await mountLiveViewer()
  await type(root, 'my draft')
  workspace.noteFileMutation('note.txt')
  await type(root, 'original')
  expect(workspace.editorDirty).toBe(false)
  expect(workspace.editorExternalChange).toBe(true)
  const banner = root.querySelector('[data-before-content] [role="alert"]')
  expect(banner).not.toBeNull()
  expect(root.querySelector('[role="toolbar"]').compareDocumentPosition(banner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(banner.compareDocumentPosition(root.querySelector('textarea')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  mocks.api.get.mockResolvedValueOnce({ data: { kind: 'text', content: 'external', revision: 'b'.repeat(64) } })
  button(root, 'Aktuelle Datei laden').click()
  await vi.waitFor(() => expect(root.querySelector('textarea')?.value).toBe('external'))
  expect(workspace.editorActive).toBe(true)
  expect(workspace.editorExternalChange).toBe(false)
  expect(mocks.confirm).not.toHaveBeenCalled()
})

it('compares without caching, leaves cancelled decisions untouched, and adopts a confirmed baseline without saving', async () => {
  const { root, workspace, preview, saveFile } = await mountLiveViewer()
  await rejectSave(root)
  const saveError = root.querySelector('[data-before-content] .file-save-error')
  expect(saveError).not.toBeNull()
  expect(root.querySelector('[role="toolbar"]').compareDocumentPosition(saveError) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  workspace.editorExternalChange = true
  const original = workspace.filePreviews['note.txt']
  const latest = { kind: 'text', content: 'external content', revision: 'b'.repeat(64) }
  mocks.api.get.mockResolvedValueOnce({ data: latest })
  button(root, 'Änderungen vergleichen').click()
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Aktuelle Datei"]')?.textContent).toBe(latest.content))
  expect(preview).toHaveBeenLastCalledWith('note.txt', { cache: false })
  expect(root.querySelector('[aria-label="Dein Entwurf"]').textContent).toBe('my draft')
  expect(workspace.filePreviews['note.txt']).toBe(original)
  expect(original.revision).toBe('a'.repeat(64))
  expect(mocks.confirm).not.toHaveBeenCalled()
  await type(root, 'original')
  expect(workspace.editorDirty).toBe(false)
  await type(root, 'my draft')

  for (const label of ['Aktuelle Datei übernehmen', 'Entwurf weiterbearbeiten']) {
    mocks.confirm.mockResolvedValueOnce(false)
    button(root, label).click()
    await nextTick()
    expect(mocks.confirm).toHaveBeenLastCalledWith(
      expect.any(String),
      label === 'Aktuelle Datei übernehmen' ? 'Entwurf verwerfen?' : 'Neue Speichergrundlage übernehmen?',
      { okLabel: label === 'Aktuelle Datei übernehmen' ? 'Verwerfen' : 'Weiterbearbeiten' }
    )
    expect(workspace.filePreviews['note.txt']).toBe(original)
    expect(workspace.filePreviews['note.txt'].revision).toBe('a'.repeat(64))
    expect(workspace.editorDraft).toBe('my draft')
    expect(workspace.editorDirty).toBe(true)
    expect(workspace.editorExternalChange).toBe(true)
    expect(root.querySelector('[aria-label="Aktuelle Datei"]')).not.toBeNull()
    expect(saveFile).toHaveBeenCalledTimes(1)
  }

  let confirmBaseline
  mocks.confirm.mockImplementationOnce(() => new Promise((resolve) => { confirmBaseline = resolve }))
  button(root, 'Entwurf weiterbearbeiten').click()
  await nextTick()
  expect(mocks.confirm).toHaveBeenLastCalledWith(
    expect.any(String), 'Neue Speichergrundlage übernehmen?', { okLabel: 'Weiterbearbeiten' }
  )
  expect(workspace.filePreviews['note.txt']).toBe(original)
  confirmBaseline(true)
  await vi.waitFor(() => expect(root.querySelector('[aria-label="Aktuelle Datei"]')).toBeNull())
  expect(workspace.filePreviews['note.txt']).toEqual(latest)
  expect(workspace.editorDraft).toBe('my draft')
  expect(workspace.editorActive).toBe(true)
  expect(workspace.editorDirty).toBe(true)
  expect(workspace.editorExternalChange).toBe(false)
  expect(mocks.api.put).toHaveBeenCalledTimes(1)
  await type(root, latest.content)
  expect(workspace.editorDirty).toBe(false)
  await type(root, 'my draft')
  mocks.api.put.mockResolvedValueOnce({ data: { revision: 'c'.repeat(64) } })
  button(root, 'Speichern').click()
  await saveFile.mock.results[1].value
  await nextTick()
  expect(saveFile).toHaveBeenLastCalledWith('note.txt', 'my draft', latest.revision)
  expect(workspace.editorDirty).toBe(false)
})

it.each(['path', 'tab', 'context'])('ignores a late conflict comparison after a %s change', async (change) => {
  const { root, workspace, preview } = await mountLiveViewer()
  workspace.activeFileTabId = 'file-a'
  await rejectSave(root)
  let resolveLatest
  mocks.api.get.mockImplementationOnce(() => new Promise((resolve) => { resolveLatest = resolve }))
  button(root, 'Änderungen vergleichen').click()
  const pending = preview.mock.results.at(-1).value
  const next = { kind: 'text', content: 'other original', revision: 'c'.repeat(64) }
  mocks.api.get.mockResolvedValue({ data: next })
  if (change === 'tab') {
    workspace.activateFileTab({
      tabId: 'file-b', kind: 'file',
      fileTarget: { scope: 'session', path: 'other.txt', name: 'other.txt', rootIdentity: 'root-id', readOnly: false }
    })
  } else {
    if (change === 'context') workspace.configure({ sessionId: 'other-session', rootIdentity: 'other-root' })
    workspace.openEntry({ kind: 'file', path: 'other.txt', name: 'other.txt' })
  }
  await vi.waitFor(() => expect(root.querySelector('textarea')?.value).toBe(next.content))
  await type(root, 'other draft')
  resolveLatest({ data: { kind: 'text', content: 'late external content', revision: 'b'.repeat(64) } })
  await pending
  await nextTick()

  expect(root.querySelector('[aria-label="Aktuelle Datei"]')).toBeNull()
  expect(workspace.viewedEntry.path).toBe('other.txt')
  expect(workspace.filePreviews['other.txt']).toEqual(next)
  expect(root.querySelector('textarea').value).toBe('other draft')
  expect(workspace.editorActive).toBe(true)
  expect(workspace.editorDirty).toBe(true)
  expect(mocks.confirm).not.toHaveBeenCalled()
  expect(mocks.api.put).toHaveBeenCalledTimes(1)
})

it('keeps a tool-context text file read-only even when its agent supports reading', async () => {
  const { root, workspace, saveFile } = await mountLiveViewer({
    path: '/project/note.txt',
    toolContext: { executionAgentId: 'historical-agent', currentDirectory: '/project' }
  })
  expect(root.querySelector('pre').textContent).toBe('original')
  expect(root.querySelector('textarea')).toBeNull()
  expect(workspace.editorActive).toBe(false)
  expect(saveFile).not.toHaveBeenCalled()
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  expect(root.querySelector('.file-viewer__toolbar')).not.toBeNull()
  expect(root.querySelector('[aria-label="Versionsverlauf öffnen"]')).toBeNull()
})

it('opens the writable OpenSCAD source in the editor and blocks returning to 3D while dirty or saving', async () => {
  const { root, workspace, render, saveFile } = await mountLiveViewer({ kind: 'openscad', path: 'model.scad' })
  expect(workspace.editorActive).toBe(false)
  expect(root.querySelector('textarea')).toBeNull()
  expect(render).toHaveBeenCalledTimes(1)
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  expect(root.querySelector('.file-viewer__toolbar')).not.toBeNull()
  button(root, 'OpenSCAD-Quelltext anzeigen').click()
  await vi.waitFor(() => expect(root.querySelector('textarea')?.value).toBe('original'))
  expect(workspace.editorActive).toBe(true)
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  expect(root.querySelector('.file-viewer__toolbar')).toBeNull()
  expect(button(root, 'OpenSCAD-3D-Modell anzeigen').closest('[data-file-menu]')).not.toBeNull()
  await type(root, 'cube(2);')
  expect(button(root, 'OpenSCAD-3D-Modell anzeigen').disabled).toBe(true)
  button(root, 'OpenSCAD-3D-Modell anzeigen').click()
  expect(workspace.editorDraft).toBe('cube(2);')
  expect(workspace.editorActive).toBe(true)
  expect(render).toHaveBeenCalledTimes(1)

  let resolveSave
  mocks.api.put.mockImplementationOnce(() => new Promise((resolve) => { resolveSave = resolve }))
  button(root, 'Speichern').click()
  await type(root, 'original')
  expect(workspace.editorDirty).toBe(false)
  expect(button(root, 'OpenSCAD-3D-Modell anzeigen').disabled).toBe(true)
  button(root, 'OpenSCAD-3D-Modell anzeigen').click()
  expect(workspace.editorActive).toBe(true)
  resolveSave({ data: { revision: 'b'.repeat(64) } })
  await saveFile.mock.results[0].value
  await type(root, 'cube(2);')
  button(root, 'OpenSCAD-3D-Modell anzeigen').click()
  await nextTick()
  expect(workspace.editorActive).toBe(false)
  expect(root.querySelector('textarea')).toBeNull()
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  expect(root.querySelector('.file-viewer__toolbar')).not.toBeNull()
  expect(render).toHaveBeenCalledTimes(2)
})

it.each(['image', 'stl'])('keeps the default toolbar for a current %s preview', async (kind) => {
  const { root } = await mountLiveViewer({ kind })
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  expect(root.querySelector('.file-viewer__toolbar')).not.toBeNull()
  expect(root.querySelector('[data-editor-toolbar]')).toBeNull()
  expect(button(root, 'Datei herunterladen').disabled).toBe(false)
})

it.each(['text', 'image'])('forwards an app toolbar that replaces the %s toolbar and keeps the region labelled', async (kind) => {
  const { root, workspace } = await mountLiveViewer({
    kind, viaApp: true, toolbar: () => h('div', { role: 'toolbar', 'data-app-toolbar': '' }, 'App-Werkzeuge')
  })
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  expect(root.querySelector('[data-app-toolbar]')).not.toBeNull()
  expect(root.querySelector('.file-viewer__toolbar, [data-editor-toolbar]')).toBeNull()
  expect(root.querySelectorAll('[role="separator"]')).toHaveLength(1)
  expect(root.querySelector('section').getAttribute('aria-label')).toBe('note.txt')
  expect(root.querySelector('section').hasAttribute('aria-labelledby')).toBe(false)
  expect(root.querySelector(kind === 'text' ? 'textarea' : 'img')).not.toBeNull()
  if (kind === 'text') {
    workspace.editorExternalChange = true
    await nextTick()
    expect(root.querySelector('[data-before-content] [role="alert"]')).not.toBeNull()
    expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  }
})

it('does not forward an absent app toolbar over the editor default', async () => {
  const { root } = await mountLiveViewer({ viaApp: true })
  expect(root.querySelectorAll('[role="toolbar"]')).toHaveLength(1)
  expect(root.querySelector('[data-editor-toolbar]')).not.toBeNull()
})

it('uses existing file actions from the editor menu without exporting an unsaved PDF or switching versions', async () => {
  const { root, workspace, toggleFullscreen } = await mountLiveViewer({ kind: 'markdown', path: 'note.md', fullscreenToggle: true })
  const download = vi.spyOn(workspace, 'download').mockImplementation(() => {})
  const revisions = vi.spyOn(workspace, 'loadRevisions').mockResolvedValue(null)
  const menu = root.querySelector('[data-file-menu]')
  expect(button(menu, 'Markdown als PDF speichern').getAttribute('href')).toBe('/print')
  expect(button(menu, 'Markdown als PDF speichern').getAttribute('rel')).toBe('noopener noreferrer')
  expect(button(menu, 'Markdown als PDF speichern').disabled).toBe(false)
  button(menu, 'Versionsverlauf öffnen').click()
  await nextTick()
  expect(revisions).toHaveBeenCalledWith('note.md')

  await type(root, 'unsaved draft')
  expect(button(menu, 'Markdown als PDF speichern').disabled).toBe(true)
  expect(button(menu, 'Versionsverlauf öffnen').disabled).toBe(true)
  button(menu, 'Versionsverlauf öffnen').click()
  expect(revisions).toHaveBeenCalledTimes(1)
  button(menu, 'Dateiinhalt kopieren').click()
  expect(copyToClipboard).toHaveBeenCalledWith('unsaved draft')
  button(menu, 'Datei herunterladen').click()
  expect(download).toHaveBeenCalledWith('note.md')
  button(menu, 'Im Vollbild öffnen').click()
  expect(toggleFullscreen).toHaveBeenCalledTimes(1)
  expect(workspace.filePreviews['note.md'].content).toBe('original')
})
