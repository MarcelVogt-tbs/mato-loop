// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'
import FileViewerPanel from '../src/components/FileViewerPanel.vue'
import { useWorkspaceStore } from '../src/stores/workspace'

vi.mock('src/boot/axios', () => ({ api: {} }))
vi.mock('quasar', () => ({
  copyToClipboard: vi.fn(),
  useQuasar: () => ({ screen: { gt: { xs: true }, lt: { sm: false } }, notify: vi.fn() })
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ resolve: () => ({ href: '/print' }) }) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm: vi.fn() }) }))
vi.mock('components/MessageContent.vue', () => ({ default: {
  props: ['content'], render() { return h('article', this.content) }
} }))
vi.mock('components/StlViewerPanel.vue', () => ({ default: {
  props: ['src'], render() { return h('div', { 'data-model-src': this.src }) }
} }))
vi.mock('components/WorkspaceFileEditor.vue', () => ({ default: { render: () => null } }))
vi.mock('components/ImageViewerPanel.vue', () => ({ default: { render: () => null } }))

let app
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
    for (const name of ['QToolbar', 'QToolbarTitle', 'QBtn', 'QBtnToggle', 'QTooltip', 'QSeparator', 'QBanner', 'QSpinner', 'QIcon', 'QCard', 'QCardSection', 'QItem', 'QItemSection', 'QItemLabel', 'QChip', 'QList']) {
      app.component(name, { render() { return h('div', this.$attrs, this.$slots.default?.()) } })
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
    expect(root.querySelector('[aria-label="Datei bearbeiten"]')).toBeNull()
    expect(root.querySelector('[aria-label="STL erzeugen und herunterladen"]')).toBeNull()
    if (kind === 'openscad') expect(root.textContent).toContain('nur den Quelltext')
    if (kind === 'markdown') expect(root.querySelector('article').textContent).toBe('old source')
    if (['text', 'openscad'].includes(kind)) expect(root.querySelector('pre').textContent).toBe('old source')
    if (kind === 'image') expect(root.querySelector('img').getAttribute('src')).toContain('/revisions/1/content?')
    if (kind === 'stl') expect(root.querySelector('[data-model-src]').dataset.modelSrc).toContain('file_id=file-id')
  }
)
