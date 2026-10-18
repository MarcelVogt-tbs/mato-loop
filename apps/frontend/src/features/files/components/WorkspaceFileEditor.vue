<template>
  <div class="workspace-editor column no-wrap" @keydown="handleKeydown" @keydown.esc.stop>
    <q-resize-observer v-if="comparison" @resize="width = $event.width" />
    <template v-if="showToolbar">
      <q-toolbar class="workspace-editor__actions q-px-xs" role="group" aria-label="Editor-Werkzeuge">
        <q-btn flat round dense icon="save" :color="dirty ? 'primary' : undefined" :loading="saving" :disable="!dirty || diffActive || readonly" aria-label="Speichern" @click="emit('save')">
          <q-tooltip>Speichern (Strg/Cmd+S)</q-tooltip>
        </q-btn>
        <q-separator vertical inset class="workspace-editor__secondary q-mx-xs" />
        <q-btn flat round dense icon="undo" :disable="!canUndo || diffActive || readonly" aria-label="Rückgängig" @click="runHistory(undo)">
          <q-tooltip>Rückgängig (Strg/Cmd+Z)</q-tooltip>
        </q-btn>
        <q-btn flat round dense icon="redo" :disable="!canRedo || diffActive || readonly" aria-label="Wiederholen" @click="runHistory(redo)">
          <q-tooltip>Wiederholen (Strg/Cmd+Umschalt+Z)</q-tooltip>
        </q-btn>
        <q-separator vertical inset class="workspace-editor__secondary q-mx-xs" />
        <q-btn flat round dense icon="search" aria-label="Suchen und ersetzen" @click="showSearch">
          <q-tooltip>Suchen und ersetzen (Strg/Cmd+F oder Strg/Cmd+H)</q-tooltip>
        </q-btn>
        <q-btn class="workspace-editor__secondary" flat round dense icon="wrap_text" :color="wrap ? 'primary' : undefined" :aria-pressed="wrap" aria-label="Zeilenumbruch" @click="toggleWrap">
          <q-tooltip>Zeilenumbruch</q-tooltip>
        </q-btn>
        <q-btn v-if="markdown" flat round dense :disable="diffActive" :icon="mode === 'preview' ? 'code' : 'visibility'" :color="mode === 'preview' ? 'primary' : undefined" :aria-pressed="mode === 'preview'" aria-label="Markdown-Vorschau" @click="mode = mode === 'source' ? 'preview' : 'source'">
          <q-tooltip>{{ mode === 'preview' ? 'Quelltext anzeigen' : 'Markdown-Vorschau' }}</q-tooltip>
        </q-btn>
        <q-btn v-if="comparison" flat round dense icon="difference" :color="diffActive ? 'primary' : undefined" :aria-pressed="diffActive" :disable="saving || (diffActive && comparison.deleted)" aria-label="Git-Diff umschalten" @click="emit('update:diffActive', !diffActive)">
          <q-tooltip>{{ comparison.deleted ? 'Datei gelöscht – nur Diff verfügbar' : diffActive ? 'Quelltext anzeigen' : 'Gespeicherten Arbeitsstand mit HEAD vergleichen' }}</q-tooltip>
        </q-btn>
        <q-space />
        <q-btn flat round dense icon="more_vert" aria-label="Weitere Editor-Aktionen">
          <q-tooltip>Weitere Aktionen</q-tooltip>
          <AppMenu class="file-actions-menu" anchor="bottom right" self="top right">
            <q-list padding>
              <q-item clickable v-close-popup :active="wrap" aria-label="Zeilenumbruch umschalten" @click="toggleWrap">
                <q-item-section avatar><q-icon name="wrap_text" /></q-item-section>
                <q-item-section>Zeilenumbruch</q-item-section>
                <q-item-section v-if="wrap" side><q-icon name="check" /></q-item-section>
              </q-item>
              <slot name="file-menu" />
              <q-separator class="q-my-xs" />
              <q-item clickable v-close-popup :disable="!dirty || saving || diffActive" aria-label="Änderungen verwerfen" @click="emit('cancel')">
                <q-item-section avatar><q-icon name="restore" /></q-item-section>
                <q-item-section>Änderungen verwerfen</q-item-section>
              </q-item>
            </q-list>
          </AppMenu>
        </q-btn>
      </q-toolbar>
      <q-separator />
    </template>
    <slot name="before-content" />
    <div v-show="!diffActive && mode === 'source'" ref="sourceRoot" class="workspace-editor__source col" />
    <div v-if="!diffActive && mode === 'preview'" class="workspace-editor__preview col scroll q-pa-md">
      <MessageContent :content="modelValue" :allow-images="false" />
    </div>
    <template v-if="diffActive">
      <div v-if="comparison?.loading" class="col flex flex-center"><q-spinner color="primary" size="32px" /></div>
      <AppStateMessage v-else-if="comparison?.error" class="col q-pa-md" icon="cloud_off" title="Diff konnte nicht geladen werden" :description="apiErrorMessage(comparison.error)">
        <template #actions><q-btn no-caps color="primary" label="Erneut versuchen" @click="emit('retry-diff')" /></template>
      </AppStateMessage>
      <AppStateMessage v-else-if="comparison?.reason" class="col q-pa-md" icon="description" title="Keine Textvorschau verfügbar" :description="diffReason" />
      <div v-else ref="diffRoot" class="workspace-editor__source col" aria-label="Git-Vergleich mit HEAD" />
    </template>
  </div>
</template>

<script setup>
import AppMenu from 'src/shared/ui/AppMenu.vue'
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { basicSetup } from 'codemirror'
import { Compartment, EditorState, StateEffect } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { indentWithTab, redo, redoDepth, undo, undoDepth } from '@codemirror/commands'
import { indentUnit, LanguageDescription } from '@codemirror/language'
import { languages } from '@codemirror/language-data'
import { openSearchPanel, search } from '@codemirror/search'
import { oneDark } from '@codemirror/theme-one-dark'
import { MergeView, unifiedMergeView } from '@codemirror/merge'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import { apiErrorMessage } from 'src/utils/api-errors'
import MessageContent from 'src/features/chat/components/MessageContent.vue'

defineOptions({ name: 'WorkspaceFileEditor' })
const props = defineProps({
  modelValue: { type: String, default: '' },
  filename: { type: String, default: '' },
  session: { type: Object, default: null },
  showToolbar: { type: Boolean, default: true },
  markdown: Boolean,
  saving: Boolean,
  dirty: Boolean,
  readonly: Boolean,
  comparison: { type: Object, default: null },
  diffActive: Boolean
})
const emit = defineEmits(['update:modelValue', 'update:session', 'update:diffActive', 'retry-diff', 'save', 'cancel'])
const $q = useQuasar()
const sourceRoot = ref(null)
const diffRoot = ref(null)
const width = ref(0)
const splitDiff = computed(() => width.value >= 720)
const diffReason = computed(() => ({
  binary: 'Binärdateien und Dateien ohne UTF-8-Kodierung haben keine Textvorschau.',
  tooLarge: 'Die Datei überschreitet die Grenze von 64 KiB je Vergleichsseite.',
  unsupported: 'Für diesen Dateityp ist kein Textvergleich verfügbar.',
  unchanged: 'Für diese Datei liegt kein aktueller Git-Diff vor.'
})[props.comparison?.reason])
const mode = ref('source')
const canUndo = ref(false)
const canRedo = ref(false)
const wrap = ref(props.session?.wrap ?? false)
const theme = new Compartment()
const wrapping = new Compartment()
const language = new Compartment()
const editable = new Compartment()
watch(() => props.readonly, (value) => {
  if (view) view.dispatch({ effects: editable.reconfigure([EditorState.readOnly.of(value), EditorView.editable.of(!value)]) })
})
let view
let diffView
let languageSupport = []
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

function extensions(content, lineBreak, comparisonSide = null) {
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
      'aria-label': comparisonSide === 'head' ? 'Datei in HEAD' : comparisonSide ? 'Aktueller Arbeitsstand' : props.markdown ? 'Markdown-Quelltext' : 'Textinhalt',
      ...(comparisonSide ? { tabindex: '0' } : {}),
      'aria-description': 'Tab rückt ein. Escape, dann Tab verlässt den Editor.',
      spellcheck: 'false'
    }),
    keymap.of([{ key: 'Mod-h', run: openSearchPanel }, indentWithTab]),
    search({ top: true }),
    language.of(languageSupport),
    editable.of([EditorState.readOnly.of(props.readonly || Boolean(comparisonSide)), EditorView.editable.of(!props.readonly && !comparisonSide)]),
    wrapping.of(wrap.value ? EditorView.lineWrapping : []),
    // App surfaces take precedence over One Dark; its syntax colors stay intact.
    EditorView.theme({
      '&': { height: comparisonSide && splitDiff.value ? 'auto' : '100%', backgroundColor: 'var(--app-shell-bg)', color: 'var(--app-text)', colorScheme: 'light' },
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
      if (comparisonSide) return
      if (update.docChanged) {
        const content = update.state.sliceDoc()
        bytes = new TextEncoder().encode(content).byteLength
        if (!syncing) emit('update:modelValue', content)
      }
      publishSession()
    }),
    EditorView.domEventHandlers({ scroll: () => { if (!comparisonSide) publishSession() } })
  ]
}

async function loadLanguage() {
  const version = ++languageVersion
  const description = LanguageDescription.matchFilename(languages, props.filename)
  languageName = description?.name || 'Text'
  publishSession()
  try {
    const support = description ? await description.load() : []
    if (view && version === languageVersion) {
      languageSupport = support
      for (const editor of editorViews()) editor.dispatch({ effects: language.reconfigure(support) })
    }
  } catch {
    if (view && version === languageVersion) {
      languageName = 'Text'
      publishSession()
      $q.notify({ type: 'warning', message: 'Syntax-Highlighting konnte nicht geladen werden. Der Text bleibt bearbeitbar.' })
    }
  }
}

async function showSearch() {
  if (props.diffActive) {
    const editor = diffView instanceof MergeView ? diffView.b : diffView
    if (editor) { openSearchPanel(editor); editor.focus() }
    return
  }
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
  for (const editor of editorViews()) editor.dispatch({ effects: wrapping.reconfigure(wrap.value ? EditorView.lineWrapping : []) })
}

function handleKeydown(event) {
  if ((!event.ctrlKey && !event.metaKey) || event.altKey) return
  const key = event.key.toLowerCase()
  if (key === 's') {
    event.preventDefault()
    if (props.dirty && !props.saving && !props.diffActive && !props.readonly) emit('save')
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
  for (const editor of editorViews()) editor.dispatch({ effects: theme.reconfigure(dark ? oneDark : []) })
})
watch([mode, () => props.diffActive], async ([value, diffActive]) => {
  if (value !== 'source' || diffActive) return
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
  if (!props.diffActive) view.focus()
  publishSession()
  void loadLanguage()
})
function editorViews() {
  return [view, ...(diffView instanceof MergeView ? [diffView.a, diffView.b] : [diffView])].filter(Boolean)
}
function destroyDiff() {
  diffView?.destroy()
  diffView = null
  diffRoot.value?.replaceChildren()
}
async function renderDiff() {
  await nextTick()
  destroyDiff()
  if (!props.diffActive || !diffRoot.value || !props.comparison || props.comparison.loading || props.comparison.error || props.comparison.reason) return
  const configuration = (content, side) => ({ doc: content, extensions: extensions(content, undefined, side) })
  const options = { collapseUnchanged: { margin: 3, minSize: 8 }, diffConfig: { timeout: 100 } }
  if (splitDiff.value) {
    diffView = new MergeView({ parent: diffRoot.value, a: configuration(props.comparison.before, 'head'), b: configuration(props.comparison.after, 'working'), ...options })
    diffView.dom.classList.add('full-height', 'scroll')
  } else {
    const config = configuration(props.comparison.after, 'working')
    diffView = new EditorView({ ...config, parent: diffRoot.value, extensions: [...config.extensions,
      unifiedMergeView({ original: props.comparison.before, mergeControls: false, ...options })
    ] })
  }
}
watch([() => props.diffActive, () => props.comparison, splitDiff], renderDiff, { immediate: true })
onBeforeUnmount(() => {
  destroyDiff()
  languageVersion += 1
  view?.destroy()
  view = null
})
</script>
