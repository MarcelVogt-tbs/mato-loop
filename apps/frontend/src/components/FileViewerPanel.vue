<template>
  <section
    ref="viewerRoot"
    class="file-viewer column no-wrap full-height"
    tabindex="-1"
    aria-labelledby="file-viewer-title"
    @keydown.esc="emit('back')"
  >
    <div class="row items-center no-wrap q-pa-md q-gutter-sm">
      <q-btn flat round dense icon="arrow_back" aria-label="Zur Dateiliste" @click="emit('back')">
        <q-tooltip>Zur Dateiliste</q-tooltip>
      </q-btn>
      <div class="col ellipsis">
        <div id="file-viewer-title" class="text-subtitle1 text-weight-bold ellipsis">{{ entry?.name }}</div>
        <div class="text-caption app-muted ellipsis">{{ fileDetails }}</div>
      </div>
      <q-btn
        v-if="editable && !workspace.editorActive"
        flat
        round
        dense
        icon="edit"
        aria-label="Datei bearbeiten"
        @click="startEditing"
      >
        <q-tooltip>Datei bearbeiten</q-tooltip>
      </q-btn>
      <q-btn v-if="!workspace.editorActive" flat round dense icon="download" aria-label="Datei herunterladen" @click="download" />
      <q-btn flat round dense icon="close" aria-label="Explorer schließen" @click="emit('close')" />
    </div>
    <q-separator />

    <WorkspaceFileEditor
      v-if="workspace.editorActive && editable"
      v-model="draft"
      class="col"
      :markdown="preview.kind === 'markdown'"
      :saving="saving"
      @save="save"
      @cancel="cancel"
    />
    <div v-else class="col file-viewer__content q-pa-md">
      <div v-if="loading" class="fit flex flex-center"><q-spinner color="primary" size="36px" /></div>
      <img
        v-else-if="preview.kind === 'image'"
        :src="imageUrl"
        :alt="entry?.name || 'Bildvorschau'"
        class="file-viewer__image"
        @error="preview = { kind: 'none', reason: 'invalidImage' }"
      />
      <div v-else-if="preview.kind === 'markdown'" class="chat-markdown file-viewer__markdown" v-html="markdownHtml" />
      <pre v-else-if="preview.kind === 'text'" class="file-viewer__text">{{ preview.content }}</pre>
      <div v-else class="fit column flex-center text-center q-pa-xl">
        <q-icon name="draft" size="64px" class="app-muted q-mb-md" />
        <div class="text-subtitle1">Keine Vorschau verfügbar</div>
        <div class="app-muted q-mt-xs">{{ unavailableMessage }}</div>
        <q-btn class="q-mt-lg" color="primary" no-caps icon="download" label="Datei herunterladen" @click="download" />
      </div>
    </div>
  </section>
</template>

<script setup>
import 'katex/dist/katex.min.css'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import WorkspaceFileEditor from 'components/WorkspaceFileEditor.vue'
import { useConfirm } from 'src/composables/confirm'
import { renderMarkdown } from 'src/utils/markdown'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileViewerPanel' })

const props = defineProps({
  entry: { type: Object, required: true }
})
const emit = defineEmits(['back', 'close'])
const $q = useQuasar()
const { confirm } = useConfirm()
const workspace = useWorkspaceStore()
const viewerRoot = ref(null)
const loading = ref(false)
const saving = ref(false)
const preview = ref({ kind: 'none', reason: 'unsupported' })
let previewVersion = 0
const draft = computed({
  get: () => workspace.editorDraft ?? '',
  set: (value) => workspace.updateEditorDraft(value, preview.value.content)
})
const imageUrl = computed(() => workspace.fileUrl('content', props.entry.path))
const editable = computed(() => (
  ['text', 'markdown'].includes(preview.value.kind)
  && (workspace.scope === 'filesystem' || props.entry.path.startsWith('files/'))
))
const markdownHtml = computed(() => renderMarkdown(preview.value.content, {
  allowImages: false,
  interactive: false
}).html)
const fileDetails = computed(() => {
  const size = props.entry?.size
  if (!Number.isFinite(size)) return props.entry?.path || ''
  return `${new Intl.NumberFormat('de-DE').format(size)} Byte · ${props.entry.path}`
})
const unavailableMessage = computed(() => ({
  invalidImage: 'Die Bilddatei ist beschädigt oder ungültig.',
  unsafeSvg: 'Die SVG-Datei enthält aktive oder externe Inhalte und kann nicht sicher angezeigt werden.',
  tooLarge: 'Die Datei ist für eine Vorschau zu groß.',
  unsupported: 'Dieser Dateityp kann nur heruntergeladen werden.'
})[preview.value.reason] || 'Dieser Dateityp kann nur heruntergeladen werden.')

async function loadPreview() {
  const version = ++previewVersion
  preview.value = { kind: 'none', reason: 'unsupported' }
  loading.value = true
  await nextTick()
  viewerRoot.value?.focus()
  try {
    const result = await workspace.preview(props.entry.path)
    if (version === previewVersion && result) {
      preview.value = result
      if (workspace.editorDraft === null) workspace.editorDraft = result.content || ''
    }
  } catch {
    if (version === previewVersion) preview.value = { kind: 'none', reason: 'unsupported' }
  } finally {
    if (version === previewVersion) loading.value = false
  }
}

function startEditing() {
  workspace.startEdit(preview.value.content)
}

async function cancel() {
  if (workspace.editorDirty) {
    const accepted = await confirm(
      'Sollen die ungespeicherten Änderungen verworfen werden?',
      'Änderungen verwerfen?',
      { okLabel: 'Verwerfen' }
    )
    if (!accepted) return
  }
  workspace.discardEdit()
}

async function save() {
  if (saving.value) return
  saving.value = true
  try {
    const result = await workspace.saveFile(props.entry.path, draft.value, preview.value.revision)
    if (!result) return
    preview.value = workspace.filePreviews[props.entry.path]
    workspace.discardEdit()
    $q.notify({ type: 'positive', message: 'Datei gespeichert.' })
  } catch {
    // The central API interceptor displays the error; keep the editor open.
  } finally {
    saving.value = false
  }
}

function warnBeforeUnload(event) {
  if (!workspace.editorDirty) return
  event.preventDefault()
  event.returnValue = ''
}

function download() {
  workspace.download(props.entry.path)
}

watch(
  () => [props.entry.path, workspace.scope, workspace.sessionId, workspace.workingDirectory],
  loadPreview,
  { immediate: true }
)

onMounted(() => window.addEventListener('beforeunload', warnBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', warnBeforeUnload))
</script>
