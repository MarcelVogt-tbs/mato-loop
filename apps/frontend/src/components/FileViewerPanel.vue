<template>
  <ImageViewerPanel
    v-if="directImage"
    :image="directImage"
    :fullscreen="fullscreen"
    :fullscreen-toggle="fullscreenToggle"
    @close="emit('close')"
    @toggle-fullscreen="emit('toggle-fullscreen')"
  />
  <section
    v-else
    ref="viewerRoot"
    class="file-viewer column no-wrap full-height"
    tabindex="-1"
    aria-labelledby="file-viewer-title"
    @keydown.esc="handleEscape"
  >
    <q-toolbar class="file-viewer__toolbar q-px-md">
      <q-btn v-if="entry" class="file-icon-action" flat round dense icon="arrow_back" aria-label="Zur Dateiliste" @click="emit('back')">
        <q-tooltip>Zur Dateiliste</q-tooltip>
      </q-btn>
      <q-toolbar-title id="file-viewer-title" class="text-subtitle1 text-weight-bold ellipsis">{{ viewerName }}</q-toolbar-title>
      <q-btn-toggle
        v-if="preview.kind === 'openscad'"
        class="file-viewer__view-toggle"
        :model-value="workspace.editorActive ? 'source' : openScadView"
        unelevated
        no-caps
        toggle-color="primary"
        :options="openScadViewOptions"
        aria-label="OpenSCAD-Ansicht wählen"
        @update:model-value="setOpenScadView"
      />
      <q-btn
        v-if="preview.kind === 'openscad' && !workspace.editorActive"
        class="file-viewer__export-stl"
        unelevated
        no-caps
        color="primary"
        icon="download"
        :label="$q.screen.gt.xs ? 'STL erzeugen' : undefined"
        :loading="exportingOpenScad"
        :disable="renderingOpenScad"
        aria-label="STL erzeugen und herunterladen"
        @click="exportOpenScad"
      >
        <q-tooltip>STL erzeugen und herunterladen</q-tooltip>
      </q-btn>
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
      <q-btn
        v-if="preview.kind === 'image' && imageUrl"
        class="file-icon-action"
        flat
        round
        dense
        icon="open_in_new"
        :href="imageUrl"
        target="_blank"
        rel="noopener noreferrer"
        aria-label="Bild in neuem Tab öffnen"
      >
        <q-tooltip>In neuem Tab öffnen</q-tooltip>
      </q-btn>
      <q-btn
        v-if="preview.kind === 'markdown' && !workspace.editorActive"
        class="file-icon-action"
        flat
        round
        dense
        icon="picture_as_pdf"
        :href="printUrl"
        target="_blank"
        rel="noopener noreferrer"
        aria-label="Markdown als PDF speichern"
      >
        <q-tooltip>Als PDF speichern</q-tooltip>
      </q-btn>
      <q-btn
        v-if="entry && workspace.scope === 'session' && !workspace.toolContext && !workspace.editorActive"
        class="file-icon-action"
        flat
        round
        dense
        icon="history"
        aria-label="Versionsverlauf öffnen"
        @click="openRevisions"
      >
        <q-tooltip>Versionsverlauf</q-tooltip>
      </q-btn>
      <q-btn v-if="!workspace.editorActive && canDownload" class="file-icon-action" flat round dense icon="download" aria-label="Datei herunterladen" @click="download">
        <q-tooltip>Datei herunterladen</q-tooltip>
      </q-btn>
      <q-btn
        v-if="fullscreenToggle"
        class="file-icon-action"
        flat
        round
        dense
        :icon="fullscreen ? 'fullscreen_exit' : 'fullscreen'"
        :aria-label="fullscreen ? 'Vollbild verlassen' : 'Im Vollbild öffnen'"
        @click="emit('toggle-fullscreen')"
      >
        <q-tooltip>{{ fullscreen ? 'Vollbild verlassen' : 'Im Vollbild öffnen' }}</q-tooltip>
      </q-btn>
      <q-btn class="file-icon-action" flat round dense icon="close" aria-label="Explorer schließen" @click="emit('close')">
        <q-tooltip>Explorer schließen</q-tooltip>
      </q-btn>
    </q-toolbar>
    <div v-if="entry" class="file-viewer__path q-px-md q-pb-sm">
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
      <q-banner v-if="workspace.editorExternalChange" dense rounded class="app-surface-tonal q-ma-sm" role="alert">
        <template #avatar><q-icon name="warning_amber" color="warning" /></template>
        Die Datei wurde außerhalb des Editors geändert. Dein ungespeicherter Entwurf bleibt erhalten.
      </q-banner>
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
          :alt="viewerName || 'Bildvorschau'"
          class="file-viewer__image"
          @load="imageLoaded"
          @error="imageFailed"
        />
      </div>
      <MessageContent
        v-else-if="preview.kind === 'markdown'"
        class="file-viewer__markdown"
        :content="preview.content"
        :allow-images="false"
      />
      <pre v-else-if="preview.kind === 'text' || (preview.kind === 'openscad' && openScadView === 'source')" class="file-viewer__text">{{ preview.content }}</pre>
      <template v-else-if="preview.kind === 'openscad' && openScadView === 'model'">
        <div v-if="renderingOpenScad" class="fit flex flex-center" aria-live="polite">
          <div class="column items-center q-gutter-sm">
            <q-spinner color="primary" size="36px" />
            <span>OpenSCAD-Modell wird gerendert …</span>
          </div>
        </div>
        <div v-else-if="openScadError" class="fit column flex-center text-center q-pa-xl" role="alert">
          <q-icon name="view_in_ar" size="64px" class="app-muted q-mb-md" />
          <div class="text-subtitle1">OpenSCAD-Modell konnte nicht gerendert werden</div>
          <div class="app-muted q-mt-xs">{{ openScadErrorMessage }}</div>
          <q-btn class="q-mt-lg" color="primary" no-caps label="Erneut versuchen" @click="renderOpenScad" />
        </div>
        <StlViewerPanel v-else-if="openScadUrl" :src="openScadUrl" format="off" />
      </template>
      <StlViewerPanel v-else-if="preview.kind === 'stl'" :src="stlUrl" :format="directModel?.format || 'stl'" />
      <div v-else class="fit column flex-center text-center q-pa-xl">
        <q-icon name="draft" size="64px" class="app-muted q-mb-md" />
        <div class="text-subtitle1">Keine Vorschau verfügbar</div>
        <div class="app-muted q-mt-xs">{{ unavailableMessage }}</div>
        <q-btn v-if="canDownload" class="q-mt-lg" color="primary" no-caps icon="download" label="Datei herunterladen" @click="download" />
      </div>
    </div>

    <q-dialog
      v-model="revisionsOpen"
      :maximized="$q.screen.lt.sm"
      aria-labelledby="file-revisions-title"
      @hide="clearRevisionPreview"
    >
      <q-card class="file-revisions-dialog app-surface column no-wrap">
        <q-toolbar>
          <q-icon name="history" color="primary" size="24px" class="q-ml-sm" />
          <q-toolbar-title id="file-revisions-title" class="text-subtitle1 text-weight-bold">Versionsverlauf</q-toolbar-title>
          <q-btn flat round dense icon="close" aria-label="Versionsverlauf schließen" v-close-popup />
        </q-toolbar>
        <q-separator />
        <q-card-section v-if="revisionsLoading" class="col flex flex-center">
          <q-spinner color="primary" size="36px" />
        </q-card-section>
        <q-card-section v-else-if="!revisions.length" class="col flex flex-center text-center app-muted">
          Für diese Datei sind keine früheren Versionen vorhanden.
        </q-card-section>
        <q-card-section v-else class="col scroll q-pa-none">
          <q-list separator>
            <q-item
              v-for="revision in revisions"
              :key="revisionId(revision)"
              clickable
              v-ripple
              :active="selectedRevisionId === revisionId(revision)"
              @click="previewRevision(revision)"
            >
              <q-item-section avatar><q-icon name="description" /></q-item-section>
              <q-item-section>
                <q-item-label>Version {{ revisionLabel(revision) }}</q-item-label>
                <q-item-label caption>{{ revisionDate(revision) }}</q-item-label>
              </q-item-section>
              <q-item-section v-if="isCurrentRevision(revision)" side>
                <q-chip dense color="primary" text-color="white">Aktuell</q-chip>
              </q-item-section>
              <q-item-section v-else-if="editable" side>
                <q-btn
                  flat round dense icon="restore" color="primary"
                  :loading="restoringRevisionId === revisionId(revision)"
                  :disable="Boolean(revisionActionPending)"
                  :aria-label="`Version ${revisionLabel(revision)} wiederherstellen`"
                  @click.stop="restoreRevision(revision)"
                ><q-tooltip>Wiederherstellen</q-tooltip></q-btn>
              </q-item-section>
            </q-item>
          </q-list>
          <q-separator v-if="selectedRevisionId" />
          <div v-if="revisionPreviewLoading" class="q-pa-xl text-center"><q-spinner color="primary" /></div>
          <div v-else-if="selectedRevisionId" class="q-pa-md">
            <div class="row items-center q-mb-sm">
              <div class="text-subtitle2">Vorschau</div>
              <q-space />
              <q-btn flat no-caps icon="download" label="Herunterladen" @click="downloadRevision" />
            </div>
            <pre class="file-viewer__text">{{ revisionPreview }}</pre>
          </div>
        </q-card-section>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRouter } from 'vue-router'
import ImageViewerPanel from 'components/ImageViewerPanel.vue'
import MessageContent from 'components/MessageContent.vue'
import StlViewerPanel from 'components/StlViewerPanel.vue'
import WorkspaceFileEditor from 'components/WorkspaceFileEditor.vue'
import { useConfirm } from 'src/composables/confirm'
import { apiErrorMessage, errorDescriptor } from 'src/utils/api-errors'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileViewerPanel' })

const props = defineProps({
  entry: { type: Object, default: null },
  image: { type: Object, default: null },
  model: { type: Object, default: null },
  fullscreen: Boolean,
  fullscreenToggle: Boolean
})
const emit = defineEmits(['back', 'close', 'navigate', 'toggle-fullscreen'])
const $q = useQuasar()
const router = useRouter()
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
const loadedImageUrl = ref('')
const openScadView = ref('model')
const renderingOpenScad = ref(false)
const exportingOpenScad = ref(false)
const openScadError = ref(null)
const openScadUrl = ref('')
const revisionsOpen = ref(false)
const revisionsLoading = ref(false)
const selectedRevisionId = ref(null)
const revisionPreviewLoading = ref(false)
const revisionPreview = ref('')
const restoringRevisionId = ref(null)
let renderController
let renderVersion = 0
let exportController
let exportVersion = 0
let previewVersion = 0
const directImage = computed(() => props.image?.url ? props.image : null)
const directModel = computed(() => props.model?.url ? props.model : null)
const viewerName = computed(() => props.entry?.name || directModel.value?.name || '')
const draft = computed({
  get: () => workspace.editorDraft ?? '',
  set: (value) => workspace.updateEditorDraft(value, preview.value.content)
})
const imageUrl = computed(() => props.entry?.path ? workspace.fileUrl('content', props.entry.path) : '')
const editable = computed(() => (
  props.entry?.path
  && !workspace.toolContext
  && ['text', 'markdown', 'openscad'].includes(preview.value.kind)
  && workspace.operationAvailable('workspace.write')
))
const canDownload = computed(() => Boolean(directModel.value) || workspace.operationAvailable('workspace.export'))
const stlUrl = computed(() => directModel.value?.url
  || (props.entry?.path ? workspace.fileUrl('content', props.entry.path) : ''))
const openScadViewOptions = computed(() => [
  {
    label: $q.screen.gt.xs ? 'Quelltext' : undefined,
    icon: 'code',
    value: 'source',
    attrs: { 'aria-label': 'OpenSCAD-Quelltext anzeigen' }
  },
  {
    label: $q.screen.gt.xs ? '3D' : undefined,
    icon: 'view_in_ar',
    value: 'model',
    disable: workspace.editorDirty,
    attrs: {
      'aria-label': 'OpenSCAD-3D-Modell anzeigen',
      title: workspace.editorDirty ? 'Speichere zuerst die Änderungen.' : undefined
    }
  }
])
const printUrl = computed(() => {
  if (!props.entry?.path || preview.value.kind !== 'markdown') return undefined
  return router.resolve({
    name: 'markdown-print',
    query: workspace.readRequestParams({
      path: props.entry.path,
      revision: preview.value.revision
    })
  }).href
})
const fileDetails = computed(() => {
  const size = props.entry?.size
  if (!Number.isFinite(size)) return props.entry?.path || ''
  return `${new Intl.NumberFormat('de-DE').format(size)} Byte · ${props.entry.path}`
})
const rootLabel = computed(() => {
  return 'Dateien'
})
const parentPath = computed(() => (
  props.entry?.path?.includes('/') ? props.entry.path.slice(0, props.entry.path.lastIndexOf('/')) : '.'
))
const breadcrumbs = computed(() => [
  { label: rootLabel.value, path: '.' },
  ...(props.entry?.path || '').split('/').filter((part) => part && part !== '.').map((label, index, parts) => ({
    label,
    path: parts.slice(0, index + 1).join('/')
  }))
])
const loadErrorMessage = computed(() => apiErrorMessage(loadError.value, 'Die Vorschau konnte nicht geladen werden.'))
const saveErrorMessage = computed(() => apiErrorMessage(saveError.value, 'Die Datei konnte nicht gespeichert werden.'))
const conflictLoadErrorMessage = computed(() => apiErrorMessage(conflictLoadError.value, 'Die aktuelle Revision konnte nicht geladen werden.'))
const openScadErrorMessage = computed(() => apiErrorMessage(openScadError.value, 'Das OpenSCAD-Modell konnte nicht gerendert werden.'))
const saveConflict = computed(() => errorDescriptor(saveError.value)?.code === 'file.changed')
const revisionState = computed(() => workspace.fileRevisions[props.entry?.path] || {})
const revisions = computed(() => revisionState.value.items || revisionState.value.revisions || [])
const currentRevisionId = computed(() => (
  revisionState.value.currentRevisionId
  ?? revisionState.value.currentRevision?.id
  ?? revisionState.value.currentRevision
  ?? preview.value.revision
))
const revisionActionPending = computed(() => restoringRevisionId.value)
const unavailableMessage = computed(() => ({
  invalidImage: 'Die Bilddatei ist beschädigt oder ungültig.',
  unsafeSvg: 'Die SVG-Datei enthält aktive oder externe Inhalte und kann nicht sicher angezeigt werden.',
  tooLarge: 'Die Datei ist für eine Vorschau zu groß.',
  unsupported: 'Dieser Dateityp kann nur heruntergeladen werden.'
})[preview.value.reason] || 'Dieser Dateityp kann nur heruntergeladen werden.')

async function loadPreview() {
  const version = ++previewVersion
  clearOpenScadRender()
  clearOpenScadExport()
  openScadView.value = 'model'
  preview.value = { kind: 'none', reason: 'unsupported' }
  loadError.value = null
  if (directModel.value) {
    preview.value = { kind: 'stl' }
    await nextTick()
    viewerRoot.value?.focus()
    return
  }
  if (!props.entry?.path) return
  loading.value = true
  await nextTick()
  viewerRoot.value?.focus()
  try {
    const result = await workspace.preview(props.entry.path)
    if (version === previewVersion && result) {
      preview.value = result
      imageLoading.value = result.kind === 'image' && loadedImageUrl.value !== imageUrl.value
      if (workspace.editorDraft === null) workspace.editorDraft = result.content || ''
      if (result.kind === 'openscad') void renderOpenScad()
    }
  } catch (error) {
    if (version === previewVersion) loadError.value = error
  } finally {
    if (version === previewVersion) loading.value = false
  }
}

function revisionId(revision) {
  return revision.id ?? revision.revisionId ?? revision.revision
}

function revisionLabel(revision) {
  return revision.label || revision.number || revision.revision || revisionId(revision)
}

function revisionDate(revision) {
  const value = revision.createdAt || revision.created_at
  return value ? new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)) : ''
}

function isCurrentRevision(revision) {
  return revision.current === true || revisionId(revision) === currentRevisionId.value
}

async function openRevisions() {
  revisionsOpen.value = true
  revisionsLoading.value = true
  try {
    await workspace.loadRevisions(props.entry.path)
  } catch {
    revisionsOpen.value = false
  } finally {
    revisionsLoading.value = false
  }
}

async function previewRevision(revision) {
  const id = revisionId(revision)
  const path = props.entry?.path
  selectedRevisionId.value = id
  revisionPreviewLoading.value = true
  revisionPreview.value = ''
  try {
    const result = await workspace.revisionPreview(path, id)
    if (
      !revisionsOpen.value
      || props.entry?.path !== path
      || selectedRevisionId.value !== id
      || !result
    ) return
    revisionPreview.value = result.content || 'Für diese Version ist keine Textvorschau verfügbar.'
  } catch {
    if (props.entry?.path === path && selectedRevisionId.value === id) {
      selectedRevisionId.value = null
    }
  } finally {
    if (props.entry?.path === path && selectedRevisionId.value === id) {
      revisionPreviewLoading.value = false
    }
  }
}

async function restoreRevision(revision) {
  const accepted = await confirm(
    `Soll Version ${revisionLabel(revision)} als neue aktuelle Version wiederhergestellt werden?`,
    'Version wiederherstellen?',
    { okLabel: 'Wiederherstellen', okColor: 'warning' }
  )
  if (!accepted) return
  const id = revisionId(revision)
  restoringRevisionId.value = id
  try {
    await workspace.restoreRevision(props.entry.path, id)
    clearRevisionPreview()
    $q.notify({ type: 'positive', message: 'Version wiederhergestellt.' })
  } finally {
    restoringRevisionId.value = null
  }
}

function downloadRevision() {
  const anchor = document.createElement('a')
  anchor.href = workspace.revisionDownloadUrl(props.entry.path, selectedRevisionId.value)
  anchor.download = props.entry.name
  anchor.click()
}

function clearRevisionPreview() {
  selectedRevisionId.value = null
  revisionPreview.value = ''
}

function startEditing() {
  clearOpenScadExport()
  saveError.value = null
  openScadView.value = 'source'
  workspace.startEdit(preview.value.content)
}

async function setOpenScadView(view) {
  if (view === 'model' && workspace.editorDirty) {
    $q.notify({ type: 'warning', message: 'Speichere zuerst die Änderungen am Quelltext.' })
    return
  }
  if (workspace.editorActive) workspace.discardEdit()
  if (view === 'source' && renderingOpenScad.value) clearOpenScadRender()
  openScadView.value = view
  if (view === 'model') await renderOpenScad()
}

async function renderOpenScad() {
  if (!props.entry?.path || openScadUrl.value || renderingOpenScad.value || workspace.editorDirty) return
  clearOpenScadRender()
  const version = ++renderVersion
  renderingOpenScad.value = true
  openScadError.value = null
  renderController = new AbortController()
  try {
    const blob = await workspace.renderOpenScad(
      props.entry.path,
      preview.value.revision,
      renderController.signal
    )
    if (!blob || version !== renderVersion || openScadView.value !== 'model') return
    openScadUrl.value = URL.createObjectURL(blob)
  } catch (error) {
    if (error?.name !== 'CanceledError' && error?.name !== 'AbortError' && version === renderVersion) {
      openScadError.value = error
    }
  } finally {
    if (version === renderVersion) renderingOpenScad.value = false
  }
}

async function exportOpenScad() {
  if (
    !props.entry?.path || renderingOpenScad.value || exportingOpenScad.value
    || workspace.editorDirty
  ) return
  const path = props.entry.path
  const revision = preview.value.revision
  const filename = `${props.entry.name.replace(/\.scad$/i, '')}.stl`
  const version = ++exportVersion
  exportingOpenScad.value = true
  exportController = new AbortController()
  try {
    const blob = await workspace.renderOpenScad(path, revision, exportController.signal, 'stl')
    if (!blob || version !== exportVersion || props.entry?.path !== path) return
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch {
    // The central Axios interceptor displays the API error.
  } finally {
    if (version === exportVersion) {
      exportController = null
      exportingOpenScad.value = false
    }
  }
}

function clearOpenScadExport() {
  exportVersion += 1
  exportController?.abort()
  exportController = null
  exportingOpenScad.value = false
}

function clearOpenScadRender() {
  renderVersion += 1
  renderController?.abort()
  renderController = null
  renderingOpenScad.value = false
  openScadError.value = null
  if (openScadUrl.value) URL.revokeObjectURL(openScadUrl.value)
  openScadUrl.value = ''
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
    clearOpenScadRender()
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
  if (!directModel.value) {
    workspace.download(props.entry.path)
    return
  }
  const anchor = document.createElement('a')
  anchor.href = directModel.value.url
    anchor.download = directModel.value.name || `modell.${directModel.value.format || 'stl'}`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

function handleEscape() {
  if (props.fullscreen) emit('toggle-fullscreen')
  else emit('back')
}

function imageFailed() {
  imageLoading.value = false
  preview.value = { kind: 'none', reason: 'invalidImage' }
}

function imageLoaded() {
  loadedImageUrl.value = imageUrl.value
  imageLoading.value = false
}

watch(
  [
    () => props.entry?.path,
    () => directModel.value?.url,
    () => workspace.scope,
    () => workspace.sessionId,
    () => workspace.workingDirectory,
    () => workspace.rootIdentity,
    () => workspace.toolContext,
    () => workspace.workspaceContext,
    () => workspace.fileChangeVersions[props.entry?.path]
  ],
  loadPreview,
  { immediate: true }
)

onMounted(() => window.addEventListener('beforeunload', warnBeforeUnload))
onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', warnBeforeUnload)
  previewVersion += 1
  clearOpenScadRender()
  clearOpenScadExport()
})
</script>

<style scoped>
.file-revisions-dialog {
  width: min(720px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
}
</style>
