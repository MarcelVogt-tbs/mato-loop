<template>
  <section
    ref="viewerRoot"
    class="file-viewer column no-wrap full-height"
    tabindex="-1"
    aria-labelledby="file-viewer-title"
    @keydown.esc="emit('back')"
  >
    <q-toolbar class="file-viewer__toolbar q-px-md">
      <q-btn class="file-icon-action" flat round dense icon="arrow_back" aria-label="Zur Dateiliste" @click="emit('back')">
        <q-tooltip>Zur Dateiliste</q-tooltip>
      </q-btn>
      <q-toolbar-title id="file-viewer-title" class="text-subtitle1 text-weight-bold ellipsis">{{ entry?.name }}</q-toolbar-title>
      <q-btn
        v-if="editable && !workspace.editorActive"
        ref="editButton"
        class="file-viewer__edit"
        unelevated
        no-caps
        color="primary"
        icon="edit"
        label="Bearbeiten"
        aria-label="Datei bearbeiten"
        @click="startEditing"
      >
        <q-tooltip>Datei bearbeiten</q-tooltip>
      </q-btn>
      <q-btn v-if="!workspace.editorActive" class="file-icon-action" flat round dense icon="download" aria-label="Datei herunterladen" @click="download">
        <q-tooltip>Datei herunterladen</q-tooltip>
      </q-btn>
      <q-btn class="file-icon-action" flat round dense icon="close" aria-label="Explorer schließen" @click="emit('close')">
        <q-tooltip>Explorer schließen</q-tooltip>
      </q-btn>
    </q-toolbar>
    <div class="file-viewer__path q-px-md q-pb-sm">
      <div class="row items-center no-wrap">
        <q-btn class="file-icon-action q-mr-xs" flat round dense icon="arrow_upward" aria-label="Zum übergeordneten Ordner" @click="emit('navigate', parentPath)">
          <q-tooltip>Zum übergeordneten Ordner</q-tooltip>
        </q-btn>
        <q-breadcrumbs class="col file-breadcrumbs" separator="/" active-color="primary">
          <q-breadcrumbs-el
            v-for="crumb in breadcrumbs"
            :key="crumb.path"
            :label="crumb.label"
            :class="{ 'cursor-pointer': crumb.path !== entry.path }"
            @click="crumb.path !== entry.path && emit('navigate', crumb.path)"
          />
        </q-breadcrumbs>
      </div>
      <div class="text-caption app-muted ellipsis q-pl-sm">{{ fileDetails }}</div>
    </div>
    <q-separator />

    <div v-if="workspace.editorActive && editable" class="col column no-wrap file-viewer__editor">
      <div v-if="saveError" class="file-save-error q-ma-sm q-pa-sm" role="alert">
        <div class="text-weight-medium">{{ saveErrorMessage }}</div>
        <div v-if="saveConflict" class="app-muted text-caption q-mt-xs">
          Dein Entwurf bleibt erhalten. Lade die aktuelle Revision und prüfe ihn vor dem erneuten Speichern.
        </div>
        <div v-if="conflictLoadError" class="text-negative text-caption q-mt-xs">{{ conflictLoadErrorMessage }}</div>
        <div class="row q-gutter-sm q-mt-sm">
          <q-btn
            v-if="saveConflict"
            outline
            no-caps
            color="primary"
            label="Aktuelle Revision laden"
            :loading="loadingLatest"
            @click="loadLatestForConflict"
          />
          <q-btn v-else flat no-caps color="primary" label="Erneut versuchen" @click="save" />
        </div>
      </div>
      <WorkspaceFileEditor
        v-model="draft"
        class="col"
        :markdown="preview.kind === 'markdown'"
        :saving="saving"
        :dirty="workspace.editorDirty"
        @save="save"
        @cancel="cancel"
      />
    </div>
    <div v-else class="col file-viewer__content q-pa-md">
      <div v-if="loading" class="fit flex flex-center"><q-spinner color="primary" size="36px" /></div>
      <div v-else-if="loadError" class="fit column flex-center text-center q-pa-xl" role="alert">
        <q-icon name="cloud_off" size="64px" class="app-muted q-mb-md" />
        <div class="text-subtitle1">Vorschau konnte nicht geladen werden</div>
        <div class="app-muted q-mt-xs">{{ loadErrorMessage }}</div>
        <q-btn class="q-mt-lg" color="primary" no-caps label="Erneut versuchen" @click="loadPreview" />
      </div>
      <div v-else-if="preview.kind === 'image'" class="fit relative-position">
        <div v-if="imageLoading" class="fit flex flex-center"><q-spinner color="primary" size="36px" /></div>
        <img
          v-show="!imageLoading"
          :src="imageUrl"
          :alt="entry?.name || 'Bildvorschau'"
          class="file-viewer__image"
          @load="imageLoading = false"
          @error="imageFailed"
        />
      </div>
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
import { apiErrorMessage, errorDescriptor } from 'src/utils/api-errors'
import { renderMarkdown } from 'src/utils/markdown'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileViewerPanel' })

const props = defineProps({
  entry: { type: Object, required: true }
})
const emit = defineEmits(['back', 'close', 'navigate'])
const $q = useQuasar()
const { confirm } = useConfirm()
const workspace = useWorkspaceStore()
const viewerRoot = ref(null)
const editButton = ref(null)
const loading = ref(false)
const imageLoading = ref(false)
const saving = ref(false)
const loadingLatest = ref(false)
const loadError = ref(null)
const saveError = ref(null)
const conflictLoadError = ref(null)
const preview = ref({ kind: 'none', reason: 'unsupported' })
let previewVersion = 0
const draft = computed({
  get: () => workspace.editorDraft ?? '',
  set: (value) => workspace.updateEditorDraft(value, preview.value.content)
})
const imageUrl = computed(() => workspace.fileUrl('content', props.entry.path))
const editable = computed(() => (
  !workspace.rootIdentity
  && ['text', 'markdown'].includes(preview.value.kind)
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
const rootLabel = computed(() => {
  if (workspace.scope === 'session') return 'Session-Dateien'
  return workspace.workingDirectory ? 'Workspace' : 'Agent-Home'
})
const parentPath = computed(() => (
  props.entry.path.includes('/') ? props.entry.path.slice(0, props.entry.path.lastIndexOf('/')) : '.'
))
const breadcrumbs = computed(() => [
  { label: rootLabel.value, path: '.' },
  ...props.entry.path.split('/').filter((part) => part && part !== '.').map((label, index, parts) => ({
    label,
    path: parts.slice(0, index + 1).join('/')
  }))
])
const loadErrorMessage = computed(() => apiErrorMessage(loadError.value, 'Die Vorschau konnte nicht geladen werden.'))
const saveErrorMessage = computed(() => apiErrorMessage(saveError.value, 'Die Datei konnte nicht gespeichert werden.'))
const conflictLoadErrorMessage = computed(() => apiErrorMessage(conflictLoadError.value, 'Die aktuelle Revision konnte nicht geladen werden.'))
const saveConflict = computed(() => errorDescriptor(saveError.value)?.code === 'file.changed')
const unavailableMessage = computed(() => ({
  invalidImage: 'Die Bilddatei ist beschädigt oder ungültig.',
  unsafeSvg: 'Die SVG-Datei enthält aktive oder externe Inhalte und kann nicht sicher angezeigt werden.',
  tooLarge: 'Die Datei ist für eine Vorschau zu groß.',
  unsupported: 'Dieser Dateityp kann nur heruntergeladen werden.'
})[preview.value.reason] || 'Dieser Dateityp kann nur heruntergeladen werden.')

async function loadPreview() {
  const version = ++previewVersion
  preview.value = { kind: 'none', reason: 'unsupported' }
  loadError.value = null
  loading.value = true
  await nextTick()
  viewerRoot.value?.focus()
  try {
    const result = await workspace.preview(props.entry.path)
    if (version === previewVersion && result) {
      preview.value = result
      imageLoading.value = result.kind === 'image'
      if (workspace.editorDraft === null) workspace.editorDraft = result.content || ''
    }
  } catch (error) {
    if (version === previewVersion) loadError.value = error
  } finally {
    if (version === previewVersion) loading.value = false
  }
}

function startEditing() {
  saveError.value = null
  workspace.startEdit(preview.value.content)
}

async function focusEditButton() {
  await nextTick()
  editButton.value?.focus?.()
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
  focusEditButton()
}

async function save() {
  if (saving.value || !workspace.editorDirty) return
  saveError.value = null
  conflictLoadError.value = null
  saving.value = true
  try {
    const result = await workspace.saveFile(props.entry.path, draft.value, preview.value.revision)
    if (!result) return
    preview.value = workspace.filePreviews[props.entry.path]
    workspace.discardEdit()
    $q.notify({ type: 'positive', message: 'Datei gespeichert.' })
    focusEditButton()
  } catch (error) {
    saveError.value = error
  } finally {
    saving.value = false
  }
}

async function loadLatestForConflict() {
  if (loadingLatest.value) return
  loadingLatest.value = true
  conflictLoadError.value = null
  try {
    const result = await workspace.preview(props.entry.path)
    if (result) {
      preview.value = result
      workspace.updateEditorDraft(draft.value, result.content || '')
      saveError.value = null
    }
  } catch (error) {
    conflictLoadError.value = error
  } finally {
    loadingLatest.value = false
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

function imageFailed() {
  imageLoading.value = false
  preview.value = { kind: 'none', reason: 'invalidImage' }
}

watch(
  () => [
    props.entry.path,
    workspace.scope,
    workspace.sessionId,
    workspace.workingDirectory,
    workspace.rootIdentity
  ],
  loadPreview,
  { immediate: true }
)

onMounted(() => window.addEventListener('beforeunload', warnBeforeUnload))
onBeforeUnmount(() => window.removeEventListener('beforeunload', warnBeforeUnload))
</script>
