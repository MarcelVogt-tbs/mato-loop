<template>
  <div ref="explorerRoot" class="column full-height no-wrap file-explorer">
    <FileViewerPanel
      v-if="workspace.viewedEntry"
      :entry="workspace.viewedEntry"
      @back="backToFiles"
      @close="closeExplorer"
    />
    <div v-show="!workspace.viewedEntry" class="column full-height no-wrap">
    <div class="row items-center no-wrap q-pa-md q-gutter-sm">
      <q-icon name="folder_open" color="primary" size="26px" />
      <div class="text-subtitle1 text-weight-bold">Agent-Dateien</div>
      <q-space />
      <q-btn flat round dense icon="refresh" aria-label="Aktualisieren" @click="workspace.load('.')" />
      <q-btn flat round dense icon="close" aria-label="Explorer schließen" @click="closeExplorer" />
    </div>
    <div class="q-px-md q-pb-md q-gutter-y-sm">
      <q-btn-toggle
        :model-value="workspace.scope"
        spread
        no-caps
        unelevated
        toggle-color="primary"
        :options="scopeOptions"
        @update:model-value="workspace.setScope"
      />
      <div class="text-caption app-muted ellipsis">{{ scopeDescription }}</div>
      <q-input v-model="workspace.filter" outlined dense clearable placeholder="Dateien filtern">
        <template #prepend><q-icon name="search" /></template>
      </q-input>
    </div>
    <q-separator />

    <q-scroll-area class="col">
      <div class="q-pa-sm" aria-label="Dateien und Ordner">
        <div
          :class="['file-tree-row row items-center no-wrap', { 'file-tree-row--selected': workspace.selectedPath === '.' }]"
          role="button"
          tabindex="0"
          data-file-path="."
          :aria-current="workspace.selectedPath === '.' ? 'true' : undefined"
          @click="workspace.selectedPath = '.'"
          @keydown.enter="workspace.selectedPath = '.'"
          @keydown.space.prevent="workspace.selectedPath = '.'"
        >
          <span class="file-tree-spacer" />
          <q-icon :name="workspace.scope === 'session' ? 'folder_special' : workspace.workingDirectory ? 'home_work' : 'home'" size="20px" />
          <span class="q-ml-sm">{{ workspace.scope === 'session' ? 'Session-Home' : workspace.workingDirectory ? 'Workspace' : 'home' }}</span>
        </div>
        <FileTreeNode
          v-for="entry in workspace.entries['.'] || []"
          :key="`${workspace.contextVersion}:${entry.path}`"
          :entry="entry"
          :filter="workspace.filter || ''"
          @open-file="openViewer"
          @rename-entry="openRenameDialog"
          @delete-entry="removeEntry"
        />
        <q-btn
          v-if="workspace.hasMore['.']"
          flat
          dense
          no-caps
          color="primary"
          label="Weitere laden"
          class="q-ml-xl"
          :disable="workspace.loading['.']"
          @click="workspace.load('.', true)"
        />
        <div v-if="workspace.loading['.']" class="q-pa-lg text-center"><q-spinner color="primary" /></div>
      </div>
    </q-scroll-area>

    <q-separator />
    <div class="q-pa-md q-gutter-sm">
      <div class="text-caption app-muted ellipsis">Ausgewählt: {{ workspace.selectedPath }}</div>
      <div class="row q-gutter-sm items-center">
        <q-btn v-if="canWrite" outline no-caps color="primary" icon="create_new_folder" label="Ordner" @click="createFolder" />
        <q-btn v-if="canWrite" outline no-caps color="primary" icon="note_add" label="Datei" @click="openCreateDialog" />
        <q-btn
          v-if="canWrite"
          outline
          no-caps
          color="primary"
          icon="upload_file"
          label="Hochladen"
          :loading="workspace.uploading"
          @click="uploadInput.click()"
        />
        <q-btn v-if="selectedIsFile" flat round icon="download" aria-label="Datei herunterladen" @click="workspace.download(selectedEntry.path)" />
        <q-btn
          v-if="workspace.scope === 'filesystem' && selectedIsDirectory"
          outline
          no-caps
          color="primary"
          icon="home_work"
          label="Als Workdir"
          @click="setWorkdir"
        />
        <q-btn
          v-if="workspace.scope === 'filesystem' && chat.activeContext?.workingDirectory"
          flat
          no-caps
          color="secondary"
          icon="link_off"
          label="Workdir lösen"
          @click="chat.updateActive({ working_directory: null })"
        />
      </div>
      <input ref="uploadInput" type="file" multiple class="hidden" @change="uploadSelected">

      <q-expansion-item v-if="chat.current" dense icon="verified_user" label="Session-Freigaben">
        <q-list dense>
          <q-item v-for="grant in permissions" :key="grant.id">
            <q-item-section avatar>
              <q-icon :name="grantDisplay(grant).icon" color="primary" />
            </q-item-section>
            <q-item-section>
              <q-item-label>{{ grantDisplay(grant).label }}</q-item-label>
              <q-item-label caption class="ellipsis">{{ grantDisplay(grant).value }}</q-item-label>
            </q-item-section>
            <q-item-section side>
              <q-btn
                flat
                round
                dense
                icon="close"
                color="negative"
                :aria-label="`${grantDisplay(grant).label} widerrufen`"
                @click="revoke(grant.id)"
              >
                <q-tooltip>Freigabe widerrufen</q-tooltip>
              </q-btn>
            </q-item-section>
          </q-item>
          <q-item v-if="!permissions.length"><q-item-section class="app-muted">Keine Freigaben</q-item-section></q-item>
        </q-list>
      </q-expansion-item>
    </div>
    </div>

    <q-dialog v-model="renameDialogOpen" persistent>
      <q-card class="file-rename-dialog">
        <q-form @submit.prevent="renameEntry">
          <q-card-section>
            <div class="text-h6">{{ renameTarget?.kind === 'directory' ? 'Ordner umbenennen' : 'Datei umbenennen' }}</div>
            <div class="text-caption app-muted q-mt-xs ellipsis">{{ renameTarget?.path }}</div>
          </q-card-section>
          <q-card-section class="q-pt-none">
            <q-input
              v-model="renameName"
              autofocus
              outlined
              label="Name"
              maxlength="255"
            />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md q-pt-none">
            <q-btn v-close-popup flat no-caps label="Abbrechen" :disable="renaming" />
            <q-btn
              unelevated
              no-caps
              color="primary"
              label="Umbenennen"
              type="submit"
              :loading="renaming"
              :disable="!canRename"
            />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog v-model="createDialogOpen" persistent>
      <q-card class="file-create-dialog">
        <q-form @submit.prevent="createFile">
          <q-card-section>
            <div class="text-h6">Neue Datei</div>
            <div class="text-caption app-muted q-mt-xs ellipsis">{{ mutationDirectory }}</div>
          </q-card-section>
          <q-card-section class="q-pt-none q-gutter-y-md">
            <q-select
              v-model="createSuffix"
              outlined
              emit-value
              map-options
              label="Dateityp"
              :options="fileTypeOptions"
            />
            <q-input
              v-model="createName"
              autofocus
              outlined
              label="Dateiname"
              maxlength="240"
              :hint="createName.trim() ? `Wird als „${createFileName}“ angelegt.` : 'Die Endung wird automatisch ergänzt.'"
            />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md q-pt-none">
            <q-btn v-close-popup flat no-caps label="Abbrechen" :disable="creating" />
            <q-btn
              unelevated
              no-caps
              color="primary"
              label="Anlegen"
              type="submit"
              :loading="creating"
              :disable="!createName.trim()"
            />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import FileTreeNode from 'components/FileTreeNode.vue'
import FileViewerPanel from 'components/FileViewerPanel.vue'
import { useConfirm } from 'src/composables/confirm'
import { permissionGrantDisplay } from 'src/utils/approval-display'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import {
  MAX_WORKSPACE_UPLOAD_BYTES,
  MAX_WORKSPACE_UPLOADS,
  withFileSuffix,
  useWorkspaceStore
} from 'stores/workspace'

defineOptions({ name: 'FileExplorerPanel' })

const $q = useQuasar()
const { confirm } = useConfirm()
const workspace = useWorkspaceStore()
const chat = useChatStore()
const approvals = useApprovalStore()
const permissions = ref([])
const uploadInput = ref(null)
const explorerRoot = ref(null)
const renameDialogOpen = ref(false)
const renameTarget = ref(null)
const renameName = ref('')
const renaming = ref(false)
const createDialogOpen = ref(false)
const createName = ref('')
const createSuffix = ref('.txt')
const creating = ref(false)
const fileTypeOptions = [
  { label: 'Textdatei (.txt)', value: '.txt' },
  { label: 'Markdown (.md)', value: '.md' }
]
const createFileName = computed(() => withFileSuffix(createName.value, createSuffix.value))
const selectedEntry = computed(() => {
  if (workspace.selectedPath === '.') return { kind: 'directory', path: '.' }
  return Object.values(workspace.entries).flat().find((item) => item.path === workspace.selectedPath)
})
const selectedIsDirectory = computed(() => selectedEntry.value?.kind === 'directory')
const selectedIsFile = computed(() => selectedEntry.value?.kind === 'file')
const targetDirectory = computed(() => {
  if (selectedIsDirectory.value) return workspace.selectedPath
  const path = workspace.selectedPath
  return path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '.'
})
const mutationDirectory = computed(() => (
  workspace.scope === 'session' && targetDirectory.value === '.' ? 'files' : targetDirectory.value
))
const canWrite = computed(() => workspace.scope === 'filesystem' || (
  mutationDirectory.value === 'files' || mutationDirectory.value.startsWith('files/')
))
const canRename = computed(() => Boolean(
  !renaming.value
  && renameName.value
  && renameTarget.value
  && renameName.value !== renameTarget.value.name
))
const scopeOptions = computed(() => [
  { label: 'Dateisystem', value: 'filesystem' },
  { label: 'Session', value: 'session', disable: !chat.current }
])
const scopeDescription = computed(() => {
  if (workspace.scope === 'session') return 'Session-Dateien · nur „files“ ist veränderbar'
  return workspace.workingDirectory
    ? `Workspace · ${workspace.workingDirectory}`
    : 'Home · noch kein Workspace ausgewählt'
})

function grantDisplay(grant) {
  return permissionGrantDisplay(grant, chat.sessions)
}

async function loadPermissions() {
  permissions.value = chat.current ? await approvals.permissions(chat.current.id) : []
}

function createFolder() {
  $q.dialog({
    title: 'Neuer Ordner',
    prompt: { model: '', type: 'text', label: 'Ordnername', outlined: true },
    cancel: true,
    ok: { label: 'Anlegen', color: 'primary' }
  }).onOk((name) => workspace.createDirectory(mutationDirectory.value, name))
}

function openCreateDialog() {
  createName.value = ''
  createSuffix.value = '.txt'
  createDialogOpen.value = true
}

async function createFile() {
  if (!createName.value.trim() || creating.value) return
  creating.value = true
  try {
    await workspace.createFile(
      mutationDirectory.value,
      createFileName.value
    )
    createDialogOpen.value = false
  } catch {
    // The central API interceptor displays the error; keep the dialog open.
  } finally {
    creating.value = false
  }
}

async function uploadSelected(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  if (!files.length) return
  if (files.length > MAX_WORKSPACE_UPLOADS) {
    $q.notify({ type: 'negative', message: `Bitte höchstens ${MAX_WORKSPACE_UPLOADS} Dateien gleichzeitig auswählen.` })
    return
  }
  const oversized = files.find((file) => file.size > MAX_WORKSPACE_UPLOAD_BYTES)
  if (oversized) {
    $q.notify({ type: 'negative', message: `„${oversized.name}“ ist größer als 64 MiB.` })
    return
  }
  try {
    const count = await workspace.uploadFiles(mutationDirectory.value, files)
    if (count) {
      $q.notify({ type: 'positive', message: `${count} ${count === 1 ? 'Datei' : 'Dateien'} hochgeladen.` })
    }
  } catch (error) {
    if (error.uploadedCount) {
      $q.notify({
        type: 'warning',
        message: error.uploadCompleted
          ? 'Der Upload wurde noch in der vorherigen Ansicht abgeschlossen.'
          : error.uploadInterrupted
          ? `${error.uploadedCount} Dateien wurden hochgeladen; die übrigen wurden nach dem Ansichtswechsel übersprungen.`
          : `${error.uploadedCount} Dateien wurden hochgeladen, bevor ein Fehler auftrat.`
      })
    } else if (error.uploadInterrupted) {
      $q.notify({ type: 'info', message: 'Der Upload wurde nach dem Ansichtswechsel abgebrochen.' })
    }
  }
}

function openViewer({ entry }) {
  if (entry?.kind !== 'file') return
  workspace.openEntry(entry)
}

async function backToFiles() {
  if (!await confirmDiscard()) return
  const path = workspace.viewedEntry?.path
  workspace.closeViewer()
  await nextTick()
  const rows = [...(explorerRoot.value?.querySelectorAll('[data-file-path]') || [])]
  const row = rows.find((item) => item.dataset.filePath === path)
    || rows.find((item) => item.dataset.filePath === '.')
  row?.focus()
}

async function focusPath(path, fallback = '.') {
  await nextTick()
  const rows = [...(explorerRoot.value?.querySelectorAll('[data-file-path]') || [])]
  const row = rows.find((item) => item.dataset.filePath === path)
    || rows.find((item) => item.dataset.filePath === fallback)
    || rows.find((item) => item.dataset.filePath === '.')
  row?.focus()
}

async function closeExplorer() {
  if (!await confirmDiscard()) return
  workspace.open = false
  await nextTick()
  document.getElementById('workspace-explorer-toggle')?.focus()
}

async function confirmDiscard() {
  if (!workspace.editorDirty) return true
  const accepted = await confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
  if (accepted) workspace.discardEdit()
  return accepted
}

async function setWorkdir() {
  const selected = workspace.selectedPath
  const current = workspace.workingDirectory
  const path = current && selected !== '.' ? `${current}/${selected}` : current || selected
  await chat.updateActive({ working_directory: path })
}

function openRenameDialog(entry) {
  workspace.selectedPath = entry.path
  renameTarget.value = entry
  renameName.value = entry.name
  renameDialogOpen.value = true
}

async function renameEntry() {
  if (!canRename.value) return
  renaming.value = true
  try {
    const renamed = await workspace.renameEntry(renameTarget.value.path, renameName.value)
    renameDialogOpen.value = false
    await loadPermissions()
    await focusPath(renamed.path, targetDirectory.value)
  } catch {
    // The central API interceptor displays the error; keep the dialog open.
  } finally {
    renaming.value = false
  }
}

async function removeEntry(entry) {
  workspace.selectedPath = entry.path
  const accepted = await confirm(
    entry.kind === 'directory'
      ? `Soll der Ordner „${entry.name}“ einschließlich aller enthaltenen Dateien und Ordner gelöscht werden?`
      : `Soll die Datei „${entry.name}“ gelöscht werden?`,
    entry.kind === 'directory' ? 'Ordner löschen' : 'Datei löschen',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  const parent = entry.path.includes('/')
    ? entry.path.slice(0, entry.path.lastIndexOf('/'))
    : '.'
  await workspace.deleteEntry(entry.path)
  workspace.selectedPath = parent
  await loadPermissions()
  await focusPath(parent)
}

async function revoke(id) {
  await approvals.revoke(chat.current.id, id)
  await loadPermissions()
}

watch(
  () => [
    chat.selectedSessionId || null,
    chat.current?.id || null,
    chat.current?.workingDirectory || null,
    chat.activeContext?.workingDirectory || null
  ],
  ([selectedSessionId, currentSessionId, currentWorkingDirectory, draftWorkingDirectory]) => {
    const sessionId = selectedSessionId || currentSessionId
    const workingDirectory = currentSessionId === sessionId
      ? currentWorkingDirectory
      : sessionId ? null : draftWorkingDirectory
    workspace.configure({ sessionId, workingDirectory })
    workspace.load('.')
    loadPermissions()
  },
  { immediate: true }
)
</script>
