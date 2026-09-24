// @vitest-environment jsdom
import { createApp, h, nextTick, shallowReactive } from 'vue'
import { Quasar, ClosePopup, QBtn, QCard, QDialog, QIcon, QItem, QItemSection, QList, QMenu, QSeparator, QSpace, QToolbar, QTooltip } from 'quasar'
import { EditorView } from '@codemirror/view'
import { isolateHistory, redo, undo, undoDepth } from '@codemirror/commands'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import WorkspaceFileEditor from '../src/features/files/components/WorkspaceFileEditor.vue'

vi.mock('quasar', () => import('quasar/dist/quasar.client.js'))

const rangeGeometry = Object.fromEntries(['getBoundingClientRect', 'getClientRects'].map((name) => [
  name, Object.getOwnPropertyDescriptor(Range.prototype, name)
]))
let app, root

beforeEach(() => {
  // jsdom has no layout engine; keep CodeMirror's real measurement scheduling.
  Object.defineProperties(Range.prototype, {
    getBoundingClientRect: { configurable: true, value: () => new DOMRect(0, 0, 1, 16) },
    getClientRects: { configurable: true, value: () => [new DOMRect(0, 0, 1, 16)] }
  })
  vi.stubGlobal('requestAnimationFrame', (callback) => window.setTimeout(() => callback(performance.now()), 16))
  vi.stubGlobal('cancelAnimationFrame', (handle) => window.clearTimeout(handle))
})

afterEach(() => {
  app?.unmount()
  root?.remove()
  document.body.classList.remove('body--dark', 'body--light')
  vi.unstubAllGlobals()
  for (const [name, descriptor] of Object.entries(rangeGeometry)) {
    if (descriptor) Object.defineProperty(Range.prototype, name, descriptor)
    else delete Range.prototype[name]
  }
})

async function mountEditor(initial = {}) {
  const props = shallowReactive({ modelValue: 'alpha alpha', filename: 'note.txt', dirty: true, ...initial })
  const onContent = vi.fn((content) => { props.modelValue = content })
  const onSession = vi.fn()
  const onSave = vi.fn()
  const onCancel = vi.fn()
  root = document.createElement('div')
  document.body.append(root)
  app = createApp({ render: () => h(WorkspaceFileEditor, {
    ...props, 'onUpdate:modelValue': onContent, 'onUpdate:session': onSession, onSave, onCancel
  }) })
  app.use(Quasar, {
    components: { QBtn, QCard, QDialog, QIcon, QItem, QItemSection, QList, QMenu, QSeparator, QSpace, QToolbar, QTooltip },
    directives: { ClosePopup }, config: { dark: false }
  })
  app.config.globalProperties.$q.dark.set(false)
  app.mount(root)
  await nextTick()
  const view = EditorView.findFromDOM(root.querySelector('.cm-editor'))
  expect(view).toBeInstanceOf(EditorView)
  return { props, view, onContent, onSession, onSave, onCancel }
}

function keydown(target, key, ctrlKey = false) {
  const event = new KeyboardEvent('keydown', { key, ctrlKey, bubbles: true, cancelable: true })
  target.dispatchEvent(event)
  return event
}

function button(label, container = root) {
  const element = [...container.querySelectorAll('button')].find((item) =>
    item.textContent.trim() === label || item.getAttribute('aria-label') === label
  )
  expect(element, label).toBeTruthy()
  return element
}

it('emits DOM typing and provides German search/replace with Ctrl+F/H/S and contained Escape', async () => {
  const { view, props, onContent, onSave } = await mountEditor()
  expect(view.contentDOM.getAttribute('aria-label')).toBe('Textinhalt')
  expect(onContent).not.toHaveBeenCalled()

  // Synthetic keys do not edit contenteditable in jsdom. Apply the browser's DOM mutation.
  const text = view.contentDOM.querySelector('.cm-line').firstChild
  text.textContent += '!'
  document.getSelection().collapse(text, text.textContent.length)
  view.contentDOM.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: '!' }))
  await vi.waitFor(() => expect(onContent).toHaveBeenLastCalledWith('alpha alpha!'))
  await nextTick()

  expect(keydown(view.contentDOM, 'f', true).defaultPrevented).toBe(true)
  await nextTick()
  const panel = root.querySelector('.cm-search')
  const search = panel.querySelector('input[aria-label="Suchen"]')
  const replace = panel.querySelector('input[aria-label="Ersetzen"]')
  expect(search.placeholder).toBe('Suchen')
  expect(replace.placeholder).toBe('Ersetzen')
  expect([...panel.querySelectorAll('label')].map((label) => label.textContent)).toEqual([
    'Groß-/Kleinschreibung', 'Regulärer Ausdruck', 'Ganzes Wort'
  ])
  button('Alle auswählen', panel)
  button('Schließen', panel)
  search.value = 'alpha'
  search.dispatchEvent(new Event('change', { bubbles: true }))
  replace.value = 'beta'
  replace.dispatchEvent(new Event('change', { bubbles: true }))
  button('Weiter', panel).click()
  expect(view.state.selection.main).toMatchObject({ from: 0, to: 5 })
  button('Zurück', panel).click()
  expect(view.state.selection.main).toMatchObject({ from: 6, to: 11 })
  button('Ersetzen', panel).click()
  expect(onContent).toHaveBeenLastCalledWith('alpha beta!')
  button('Alle ersetzen', panel).click()
  expect(onContent).toHaveBeenLastCalledWith('beta beta!')
  await nextTick()

  const escaped = vi.fn()
  root.addEventListener('keydown', escaped)
  keydown(search, 'Escape')
  expect(root.querySelector('.cm-search')).toBeNull()
  keydown(view.contentDOM, 'Escape')
  expect(escaped).not.toHaveBeenCalled()
  root.removeEventListener('keydown', escaped)

  expect(keydown(view.contentDOM, 'h', true).defaultPrevented).toBe(true)
  await nextTick()
  expect(root.querySelector('.cm-search input[aria-label="Ersetzen"]')).not.toBeNull()
  button('Schließen').click()
  button('Suchen und ersetzen').click()
  await nextTick()
  expect(root.querySelector('.cm-search')).not.toBeNull()

  expect(keydown(view.contentDOM, 's', true).defaultPrevented).toBe(true)
  expect(onSave).toHaveBeenCalledOnce()
  props.saving = true
  await nextTick()
  expect(keydown(view.contentDOM, 's', true).defaultPrevented).toBe(true)
  props.saving = false
  props.dirty = false
  await nextTick()
  expect(keydown(view.contentDOM, 's', true).defaultPrevented).toBe(true)
  expect(onSave).toHaveBeenCalledOnce()
})

it.each([
  ['CRLF', 'alpha\r\nbeta\r\n'],
  ['BOM', '\ufeffalpha\nbeta\n'],
  ['BOM and mixed line endings', '\ufeffalpha\r\nbeta\ngamma\rdelta\r\n']
])('preserves %s on open and edit without emitting a normalized baseline', async (_, content) => {
  const { view, onContent, onSession } = await mountEditor({ modelValue: content })
  expect(view.state.sliceDoc()).toBe(content)
  expect(onContent).not.toHaveBeenCalled()
  const from = view.state.sliceDoc().indexOf('alpha')
  view.dispatch({ changes: { from, to: from + 5, insert: 'ALPHA' }, userEvent: 'input.type' })
  await nextTick()
  const edited = content.replace('alpha', 'ALPHA')
  expect(view.state.sliceDoc()).toBe(edited)
  expect(onContent).toHaveBeenCalledExactlyOnceWith(edited)
  expect(onSession.mock.lastCall[0].bytes).toBe(new TextEncoder().encode(edited).byteLength)
  expect(undo(view)).toBe(true)
  expect(onContent).toHaveBeenLastCalledWith(content)
})

it('restores cursor and undo/redo with fresh listeners and keeps history through theme changes', async () => {
  const first = await mountEditor({ modelValue: 'alpha\nbeta' })
  first.view.dispatch({ changes: { from: 5, insert: '!' }, selection: { anchor: 6 }, userEvent: 'input.type' })
  first.view.dispatch({ selection: { anchor: 9 } })
  await nextTick()
  const saved = first.onSession.mock.lastCall[0]
  expect(saved).toMatchObject({ line: 2, column: 3 })
  expect(undoDepth(saved.state)).toBe(1)
  app.unmount()
  root.remove()
  first.onContent.mockClear()
  first.onSession.mockClear()

  const restored = await mountEditor({ modelValue: 'alpha!\nbeta', session: saved })
  expect(restored.view).not.toBe(first.view)
  expect(restored.view.state.selection.main.head).toBe(9)
  expect(undoDepth(restored.view.state)).toBe(1)
  expect(restored.onContent).not.toHaveBeenCalled()

  for (const dark of [true, false]) {
    app.config.globalProperties.$q.dark.set(dark)
    await nextTick()
    expect(restored.view.state.facet(EditorView.darkTheme)).toBe(dark)
    expect(restored.view.state.selection.main.head).toBe(9)
    expect(undoDepth(restored.view.state)).toBe(1)
    expect(restored.onContent).not.toHaveBeenCalled()
  }

  restored.onSession.mockClear()
  expect(undo(restored.view)).toBe(true)
  await nextTick()
  expect(restored.onContent).toHaveBeenCalledExactlyOnceWith('alpha\nbeta')
  expect(restored.onSession.mock.lastCall[0].state).toBe(restored.view.state)
  expect(redo(restored.view)).toBe(true)
  await nextTick()
  expect(restored.onContent).toHaveBeenLastCalledWith('alpha!\nbeta')
  restored.view.dispatch({ changes: { from: restored.view.state.doc.length, insert: '?' }, userEvent: 'input.type' })
  await nextTick()
  expect(restored.onContent).toHaveBeenLastCalledWith('alpha!\nbeta?')
  expect(first.onContent).not.toHaveBeenCalled()
  expect(first.onSession).not.toHaveBeenCalled()
})

it('keeps the original separator when edits leave only CRLF in a mixed-ending document', async () => {
  const first = await mountEditor({ modelValue: 'a\r\nb\n' })
  first.view.dispatch({ changes: { from: 4, to: 5 }, userEvent: 'delete.backward' })
  await nextTick()
  const saved = first.onSession.mock.lastCall[0]
  app.unmount()
  root.remove()
  const restored = await mountEditor({ modelValue: 'a\r\nb', session: saved })
  expect(restored.view.state.sliceDoc()).toBe('a\r\nb')
  restored.view.dispatch({ changes: { from: 4, insert: '!' }, userEvent: 'input.type', annotations: isolateHistory.of('before') })
  expect(restored.onContent).toHaveBeenLastCalledWith('a\r\nb!')
  expect(undo(restored.view)).toBe(true)
  expect(restored.onContent).toHaveBeenLastCalledWith('a\r\nb')
})

it('previews the unsaved Markdown draft safely without replacing the editor', async () => {
  const { view, onContent, onSave } = await mountEditor({ markdown: true, modelValue: '# Original' })
  expect(view.contentDOM.getAttribute('aria-label')).toBe('Markdown-Quelltext')
  const draft = [
    '# Entwurf', '', '**Ungespeichert**', '',
    '<script>alert(1)</script>', '<img src="x" onerror="alert(2)">', '',
    '[Gefahr](javascript:alert(3))', '', '![Extern](https://example.com/pixel.png)'
  ].join('\n')
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: draft }, userEvent: 'input.type' })
  await nextTick()
  button('Markdown-Vorschau').click()
  await nextTick()
  const preview = root.querySelector('.workspace-editor__preview')
  expect(preview.querySelector('h1')?.textContent).toBe('Entwurf')
  expect(preview.querySelector('strong')?.textContent).toBe('Ungespeichert')
  expect(preview.textContent).toContain('[Bild: Extern]')
  expect(preview.textContent).not.toContain('Original')
  expect(preview.querySelector('script, img, [onerror], a[href^="javascript:"], [data-markdown-image]')).toBeNull()
  expect(root.querySelector('.workspace-editor__source').style.display).toBe('none')
  expect(onContent).toHaveBeenCalledExactlyOnceWith(draft)
  expect(onSave).not.toHaveBeenCalled()

  button('Markdown-Vorschau').click()
  await nextTick()
  expect(root.querySelector('.workspace-editor__preview')).toBeNull()
  expect(EditorView.findFromDOM(root.querySelector('.cm-editor'))).toBe(view)
  expect(undo(view)).toBe(true)
  expect(onContent).toHaveBeenLastCalledWith('# Original')
})

it('uses real undo/redo icon actions independently of saving and keeps discard in the menu', async () => {
  const { props, view, onContent, onCancel } = await mountEditor({ dirty: false, markdown: true })
  expect(root.querySelectorAll('[aria-label="Editor-Werkzeuge"]')).toHaveLength(1)
  expect(button('Speichern').disabled).toBe(true)
  expect(button('Rückgängig').disabled).toBe(true)
  expect(button('Wiederholen').disabled).toBe(true)
  expect(root.querySelector('[aria-label="Änderungen verwerfen"]')).toBeNull()
  view.dispatch({ changes: { from: 0, insert: 'new ' }, userEvent: 'input.type' })
  await nextTick()
  expect(button('Rückgängig').disabled).toBe(false)
  props.saving = true
  button('Markdown-Vorschau').click()
  await nextTick()
  button('Rückgängig').click()
  await nextTick()
  await nextTick()
  expect(onContent).toHaveBeenLastCalledWith('alpha alpha')
  expect(root.querySelector('.workspace-editor__preview')).toBeNull()
  expect(button('Rückgängig').disabled).toBe(true)
  expect(button('Wiederholen').disabled).toBe(false)
  button('Wiederholen').click()
  await nextTick()
  await nextTick()
  expect(onContent).toHaveBeenLastCalledWith('new alpha alpha')
  expect(onCancel).not.toHaveBeenCalled()

  props.saving = false
  props.dirty = true
  await nextTick()
  button('Weitere Editor-Aktionen').click()
  await vi.waitFor(() => expect(document.querySelector('[aria-label="Zeilenumbruch umschalten"]')).not.toBeNull())
  document.querySelector('[aria-label="Zeilenumbruch umschalten"]').click()
  await nextTick()
  expect(button('Zeilenumbruch').getAttribute('aria-pressed')).toBe('true')
  expect(onCancel).not.toHaveBeenCalled()
  await vi.waitFor(() => expect(document.querySelector('[aria-label="Änderungen verwerfen"]')).toBeNull())
  button('Weitere Editor-Aktionen').click()
  await vi.waitFor(() => expect(document.querySelector('[aria-label="Änderungen verwerfen"]')).not.toBeNull())
  document.querySelector('[aria-label="Änderungen verwerfen"]').click()
  expect(onCancel).toHaveBeenCalledOnce()
})
