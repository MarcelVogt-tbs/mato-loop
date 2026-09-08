<template>
  <div class="workspace-editor column no-wrap" @keydown="handleKeydown" @keydown.esc.stop>
    <template v-if="showToolbar">
      <q-toolbar class="workspace-editor__actions q-px-xs" role="group" aria-label="Editor-Werkzeuge">
        <q-btn flat round dense icon="save" :color="dirty ? 'primary' : undefined" :loading="saving" :disable="!dirty" aria-label="Speichern" @click="emit('save')">
          <q-tooltip>Speichern (Strg/Cmd+S)</q-tooltip>
        </q-btn>
        <q-separator vertical inset class="workspace-editor__secondary q-mx-xs" />
        <q-btn flat round dense icon="undo" :disable="!canUndo" aria-label="Rückgängig" @click="runHistory(undo)">
          <q-tooltip>Rückgängig (Strg/Cmd+Z)</q-tooltip>
        </q-btn>
        <q-btn flat round dense icon="redo" :disable="!canRedo" aria-label="Wiederholen" @click="runHistory(redo)">
          <q-tooltip>Wiederholen (Strg/Cmd+Umschalt+Z)</q-tooltip>
        </q-btn>
        <q-separator vertical inset class="workspace-editor__secondary q-mx-xs" />
        <q-btn flat round dense icon="search" aria-label="Suchen und ersetzen" @click="showSearch">
          <q-tooltip>Suchen und ersetzen (Strg/Cmd+F oder Strg/Cmd+H)</q-tooltip>
        </q-btn>
        <q-btn class="workspace-editor__secondary" flat round dense icon="wrap_text" :color="wrap ? 'primary' : undefined" :aria-pressed="wrap" aria-label="Zeilenumbruch" @click="toggleWrap">
          <q-tooltip>Zeilenumbruch</q-tooltip>
        </q-btn>
        <q-btn v-if="markdown" flat round dense :icon="mode === 'preview' ? 'code' : 'visibility'" :color="mode === 'preview' ? 'primary' : undefined" :aria-pressed="mode === 'preview'" aria-label="Markdown-Vorschau" @click="mode = mode === 'source' ? 'preview' : 'source'">
          <q-tooltip>{{ mode === 'preview' ? 'Quelltext anzeigen' : 'Markdown-Vorschau' }}</q-tooltip>
        </q-btn>
        <q-space />
        <q-btn flat round dense icon="more_vert" aria-label="Weitere Editor-Aktionen">
          <q-tooltip>Weitere Aktionen</q-tooltip>
          <q-menu class="file-actions-menu" anchor="bottom right" self="top right">
            <q-list padding>
              <q-item clickable v-close-popup :active="wrap" aria-label="Zeilenumbruch umschalten" @click="toggleWrap">
                <q-item-section avatar><q-icon name="wrap_text" /></q-item-section>
                <q-item-section>Zeilenumbruch</q-item-section>
                <q-item-section v-if="wrap" side><q-icon name="check" /></q-item-section>
              </q-item>
              <slot name="file-menu" />
              <q-separator class="q-my-xs" />
              <q-item clickable v-close-popup :disable="!dirty || saving" aria-label="Änderungen verwerfen" @click="emit('cancel')">
                <q-item-section avatar><q-icon name="restore" /></q-item-section>
                <q-item-section>Änderungen verwerfen</q-item-section>
              </q-item>
            </q-list>
          </q-menu>
        </q-btn>
      </q-toolbar>
      <q-separator />
    </template>
    <slot name="before-content" />
    <div v-show="mode === 'source'" ref="sourceRoot" class="workspace-editor__source col" />
    <div v-if="mode === 'preview'" class="workspace-editor__preview col scroll q-pa-md">
      <MessageContent :content="modelValue" :allow-images="false" />
    </div>
  </div>
</template>

<script setup>
import { markRaw, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { basicSetup } from 'codemirror'
import { Compartment, EditorState, StateEffect } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { indentWithTab, redo, redoDepth, undo, undoDepth } from '@codemirror/commands'
import { indentUnit, LanguageDescription } from '@codemirror/language'
import { languages } from '@codemirror/language-data'
import { openSearchPanel, search } from '@codemirror/search'
import { oneDark } from '@codemirror/theme-one-dark'
import MessageContent from 'components/MessageContent.vue'

defineOptions({ name: 'WorkspaceFileEditor' })
const props = defineProps({
  modelValue: { type: String, default: '' },
  filename: { type: String, default: '' },
  session: { type: Object, default: null },
  showToolbar: { type: Boolean, default: true },
  markdown: Boolean,
  saving: Boolean,
  dirty: Boolean
})
const emit = defineEmits(['update:modelValue', 'update:session', 'save', 'cancel'])
const $q = useQuasar()
const sourceRoot = ref(null)
const mode = ref('source')
const canUndo = ref(false)
const canRedo = ref(false)
const wrap = ref(props.session?.wrap ?? false)
const theme = new Compartment()
const wrapping = new Compartment()
const language = new Compartment()
let view
let languageName = 'Text'
let languageVersion = 0
let syncing = false
let bytes = 0

function publishSession() {
  if (!view) return
  const { state } = view
  canUndo.value = undoDepth(state) > 0
  canRedo.value = redoDepth(state) > 0
  const line = state.doc.lineAt(state.selection.main.head)
  emit('update:session', {
    state: markRaw(state), scroll: markRaw(view.scrollSnapshot()),
    wrap: wrap.value, line: line.number, column: state.selection.main.head - line.from + 1,
    lines: state.doc.lines, bytes, language: languageName
  })
}

function extensions(content, lineBreak) {
  // Preserve CRLF/CR files; mixed line endings keep their literal CR characters.
  const endings = new Set(content.match(/\r\n|\r|\n/g))
  const separator = lineBreak ?? (endings.size === 1 ? [...endings][0] : '\n')
  return [
    basicSetup,
    EditorState.lineSeparator.of(separator),
    EditorState.tabSize.of(2),
    indentUnit.of('  '),
    EditorState.phrases.of({
      'Find': 'Suchen', 'Replace': 'Ersetzen', 'next': 'Weiter', 'previous': 'Zurück',
      'all': 'Alle auswählen', 'match case': 'Groß-/Kleinschreibung', 'regexp': 'Regulärer Ausdruck',
      'by word': 'Ganzes Wort', 'replace': 'Ersetzen', 'replace all': 'Alle ersetzen',
      'close': 'Schließen', 'Go to line': 'Gehe zu Zeile', 'go': 'Los',
      'Fold line': 'Zeile einklappen', 'Unfold line': 'Zeile ausklappen',
      'to': 'bis', 'folded code': 'Eingeklappter Code', 'unfold': 'Ausklappen',
      'current match': 'Aktueller Treffer', 'on line': 'in Zeile',
      'replaced match on line $': 'Treffer in Zeile $ ersetzt', 'replaced $ matches': '$ Treffer ersetzt'
    }),
    EditorView.contentAttributes.of({
      'aria-label': props.markdown ? 'Markdown-Quelltext' : 'Textinhalt',
      'aria-description': 'Tab rückt ein. Escape, dann Tab verlässt den Editor.',
      spellcheck: 'false'
    }),
    keymap.of([{ key: 'Mod-h', run: openSearchPanel }, indentWithTab]),
    search({ top: true }),
    language.of([]),
    wrapping.of(wrap.value ? EditorView.lineWrapping : []),
    // App surfaces take precedence over One Dark; its syntax colors stay intact.
    EditorView.theme({
      '&': { height: '100%', backgroundColor: 'var(--app-shell-bg)', color: 'var(--app-text)', colorScheme: 'light' },
      'body.body--dark &': { colorScheme: 'dark' },
      '.cm-scroller': { overflow: 'auto', fontFamily: 'monospace', lineHeight: '1.55' },
      '.cm-content': { caretColor: 'var(--app-text)' },
      '.cm-gutters': { backgroundColor: 'var(--app-surface-tonal)', color: 'var(--app-muted)', borderColor: 'var(--app-surface-border)' },
      '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'var(--app-hover)' },
      '.cm-panels': {
        backgroundColor: 'var(--app-surface)', color: 'var(--app-text)',
        fontSize: '0.875rem', flexShrink: '0', maxHeight: '50%', overflow: 'auto'
      },
      '.cm-panels.cm-panels-top': { borderBottom: '1px solid var(--app-surface-border)' },
      '.cm-panels.cm-panels-bottom': { borderTop: '1px solid var(--app-surface-border)' },
      '.cm-panel .cm-textfield': {
        background: 'transparent', color: 'var(--app-text)', border: '1px solid var(--app-muted)',
        borderRadius: '4px', padding: '6px 12px', minHeight: '36px', font: 'inherit',
        maxWidth: '100%', boxSizing: 'border-box'
      },
      '.cm-panel .cm-textfield::placeholder': { color: 'var(--app-muted)', opacity: '1' },
      '.cm-panel .cm-textfield:focus': { outline: '1px solid var(--q-primary)', borderColor: 'var(--q-primary)' },
      '.cm-panel button': {
        background: 'transparent', color: 'var(--app-text)', border: '0', borderRadius: '4px',
        padding: '6px 12px', minHeight: '36px', font: 'inherit', fontWeight: '500', cursor: 'pointer'
      },
      '.cm-panel button:hover, .cm-panel button:active': { background: 'var(--app-hover)' },
      '.cm-panel button:focus-visible, .cm-panel input[type=checkbox]:focus-visible': {
        outline: '2px solid var(--q-primary)', outlineOffset: '2px'
      },
      '.cm-panel input[type=checkbox]': { accentColor: 'var(--q-primary)', cursor: 'pointer' },
      '.cm-panel label': { cursor: 'pointer' },
      '.cm-panel.cm-search': { paddingRight: '36px' },
      '.cm-panel.cm-search input, .cm-panel.cm-search button, .cm-panel.cm-search label': { fontSize: 'inherit' },
      '.cm-panel.cm-search button[name=close]': { color: 'inherit', minWidth: '28px', minHeight: '28px' },
      '@media (pointer: coarse)': {
        '.cm-panel.cm-search': { paddingRight: '56px', fontSize: '16px' },
        '.cm-panel.cm-search .cm-textfield, .cm-panel.cm-search button, .cm-panel.cm-search button[name=close]': { minHeight: '48px', minWidth: '48px' },
        '.cm-panel.cm-search label': { display: 'inline-flex', alignItems: 'center', minHeight: '48px' }
      }
    }),
    theme.of($q.dark.isActive ? oneDark : []),
    EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        const content = update.state.sliceDoc()
        bytes = new TextEncoder().encode(content).byteLength
        if (!syncing) emit('update:modelValue', content)
      }
      publishSession()
    }),
    EditorView.domEventHandlers({ scroll: () => { publishSession() } })
  ]
}

async function loadLanguage() {
  const version = ++languageVersion
  const description = LanguageDescription.matchFilename(languages, props.filename)
  languageName = description?.name || 'Text'
  publishSession()
  try {
    const support = description ? await description.load() : []
    if (view && version === languageVersion) view.dispatch({ effects: language.reconfigure(support) })
  } catch {
    if (view && version === languageVersion) {
      languageName = 'Text'
      publishSession()
      $q.notify({ type: 'warning', message: 'Syntax-Highlighting konnte nicht geladen werden. Der Text bleibt bearbeitbar.' })
    }
  }
}

async function showSearch() {
  mode.value = 'source'
  await nextTick()
  if (view) openSearchPanel(view)
}

async function runHistory(command) {
  mode.value = 'source'
  await nextTick()
  if (!view) return
  command(view)
  view.focus()
}

function toggleWrap() {
  wrap.value = !wrap.value
  view?.dispatch({ effects: wrapping.reconfigure(wrap.value ? EditorView.lineWrapping : []) })
}

function handleKeydown(event) {
  if ((!event.ctrlKey && !event.metaKey) || event.altKey) return
  const key = event.key.toLowerCase()
  if (key === 's') {
    event.preventDefault()
    if (props.dirty && !props.saving) emit('save')
  } else if (['f', 'h'].includes(key) && !event.defaultPrevented) {
    event.preventDefault()
    void showSearch()
  }
}

watch(() => props.modelValue, (content) => {
  if (!view || content === view.state.sliceDoc()) return
  syncing = true
  // A reload/discard is a new baseline, not an edit that Undo should resurrect.
  view.setState(EditorState.create({ doc: content, extensions: extensions(content) }))
  bytes = new TextEncoder().encode(content).byteLength
  syncing = false
  publishSession()
  void loadLanguage()
})
watch(() => props.filename, loadLanguage)
watch(() => $q.dark.isActive, (dark) => {
  view?.dispatch({ effects: theme.reconfigure(dark ? oneDark : []) })
})
watch(mode, async (value) => {
  if (value !== 'source') return
  await nextTick()
  view?.requestMeasure()
  view?.focus()
})

onMounted(() => {
  const saved = props.session
  const restore = saved?.state?.sliceDoc() === props.modelValue
  const configuration = extensions(props.modelValue, restore ? saved.state.lineBreak : undefined)
  const state = restore
    ? saved.state.update({ effects: StateEffect.reconfigure.of(configuration) }).state
    : EditorState.create({ doc: props.modelValue, extensions: configuration })
  bytes = new TextEncoder().encode(props.modelValue).byteLength
  view = new EditorView({ state, parent: sourceRoot.value, scrollTo: restore ? saved.scroll : undefined })
  view.focus()
  publishSession()
  void loadLanguage()
})
onBeforeUnmount(() => {
  languageVersion += 1
  view?.destroy()
  view = null
})
</script>
