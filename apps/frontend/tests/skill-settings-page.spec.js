// @vitest-environment jsdom
import { createApp, h, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { EditorView } from '@codemirror/view'
import { Notify, Quasar } from 'quasar'
import { afterEach, expect, it, vi } from 'vitest'
import { quasarOptions } from './quasar-options'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))
vi.mock('src/boot/axios', () => ({ api: { get: vi.fn(), put: vi.fn() } }))
const { confirm } = vi.hoisted(() => ({ confirm: vi.fn(async () => false) }))
vi.mock('src/composables/confirm', () => ({ useConfirm: () => ({ confirm }) }))

import Page from '../src/features/skills/SkillSettingsPage.vue'
import { api } from 'src/boot/axios'
import { useSkillStore } from '../src/stores/skills'

let app, root
afterEach(() => {
  app?.unmount()
  root?.remove()
  delete Range.prototype.getBoundingClientRect
  delete Range.prototype.getClientRects
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

async function selectFile(path) {
  document.querySelector('.skill-dialog [role="combobox"]').click()
  let option
  await vi.waitFor(() => {
    option = [...document.querySelectorAll('[role="option"]')].find((item) => item.textContent.trim() === path)
    expect(option).toBeTruthy()
  })
  option.click()
  await nextTick()
}

it.each(['text', 'executable'])('keeps %s edits made during a pending save unsaved', async (change) => {
  // jsdom cannot measure CodeMirror ranges; keep the real editor and its update events.
  Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 1, 16)
  Range.prototype.getClientRects = () => [new DOMRect(0, 0, 1, 16)]
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const skill = { id: 'skill', name: 'example', scope: 'personal', enabled: true, document: '# Instructions', revision: 'a', resourcePaths: ['SKILL.md', 'script.py'] }
  api.get.mockImplementation(async (url) => {
    if (url === '/skills/settings') return { data: { personalSkillsEnabled: true } }
    if (url === '/skills') return { data: { items: [skill] } }
    return { data: new TextEncoder().encode('print(1)\n').buffer, headers: { etag: '"a"', 'x-skill-executable': 'false' } }
  })
  let finishSave
  api.put.mockImplementation(() => new Promise((resolve) => { finishSave = resolve }))
  root = document.createElement('div')
  document.body.append(root)
  const pinia = createPinia()
  app = createApp({ render: () => h(Page) })
  app.use(pinia).use(Quasar, { ...quasarOptions, plugins: { Notify } })
  app.mount(root)
  await vi.waitFor(() => expect(root.querySelector('[aria-label="example bearbeiten"]')).not.toBeNull())
  root.querySelector('[aria-label="example bearbeiten"]').click()
  await vi.waitFor(() => expect(document.querySelector('.skill-dialog .cm-editor')).not.toBeNull())
  await selectFile('script.py')
  await vi.waitFor(() => expect(document.querySelector('.cm-content').textContent).toContain('print(1)'))
  const editor = EditorView.findFromDOM(document.querySelector('.cm-editor'))
  editor.dispatch({ changes: { from: 0, insert: '# Saved edit\n' }, userEvent: 'input.type' })
  await nextTick()
  editor.contentDOM.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true }))
  await vi.waitFor(() => expect(api.put).toHaveBeenCalledOnce())
  if (change === 'text') {
    const view = EditorView.findFromDOM(document.querySelector('.cm-editor'))
    view.dispatch({ changes: { from: 0, insert: '# Later edit\n' }, userEvent: 'input.type' })
  } else {
    document.querySelector('.skill-dialog [role="checkbox"]').click()
  }
  await nextTick()
  finishSave({ data: { ...skill, revision: 'b' } })
  await vi.waitFor(() => expect(useSkillStore(pinia).saving).toBe(false))
  await nextTick()
  await selectFile('SKILL.md')
  await vi.waitFor(() => expect(confirm).toHaveBeenCalledWith('Ungespeicherte Änderungen verwerfen?', 'Datei wechseln'))
})
