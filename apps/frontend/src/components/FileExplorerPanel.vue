<template>
  <div ref="explorerRoot" class="column full-height no-wrap file-explorer">
    <FileViewerPanel
      v-if="workspace.viewedEntry && !viewerFullscreen"
      :entry="workspace.viewedEntry"
      :fullscreen-toggle="$q.screen.gt.sm"
      @back="backToFiles"
      @close="closeExplorer"
      @navigate="navigateFromViewer"
      @toggle-fullscreen="viewerFullscreen = true"
    />
    <div v-show="!workspace.viewedEntry" class="column full-height no-wrap">
    <ControlCenterContentHeader
      icon="folder_open"
      title="Dateien"
      integration-id="files"
      refreshable
      :refreshing="workspace.loading[workspace.directoryPath]"
      :refresh-disabled="!explorerAvailable"
      :custom-menu="hasOverflowActions"
      :can-move-left="integrationIndex > 0"
      :can-move-right="integrationIndex >= 0 && integrationIndex < controlCenter.tabs.length - 1"
      @refresh="refreshFiles"
      @close="emit('close')"
      @remove="emit('remove', 'files')"
      @move="emit('move', 'files', $event)"
    >
      <template #menu>
        <template v-if="hasOverflowActions">
            <q-item
              v-if="!workspace.toolContext && workspace.scope === 'filesystem' && selectedIsDirectory && workspace.operationAvailable('filesystem.list')"
              clickable
              v-ripple
              v-close-popup
              :disable="chat.running"
              @click="setWorkdir"
            >
              <q-item-section avatar><q-icon name="home_work" /></q-item-section>
              <q-item-section>Als aktuellen Ordner verwenden</q-item-section>
            </q-item>
            <q-item
              v-if="!workspace.toolContext && workspace.scope === 'filesystem' && explorerAvailable"
              clickable
              v-ripple
              v-close-popup
              @click="openAbsolutePath"
            >
              <q-item-section avatar><q-icon name="drive_file_move" /></q-item-section>
              <q-item-section>Absoluten Pfad öffnen</q-item-section>
            </q-item>
            <q-item v-if="chat.current" clickable v-ripple v-close-popup @click="openPermissionsDialog">
              <q-item-section avatar><q-icon name="verified_user" /></q-item-section>
              <q-item-section>Session-Freigaben</q-item-section>
            </q-item>
        </template>
      </template>
    </ControlCenterContentHeader>
    <div class="file-explorer__controls q-px-md q-pb-md q-gutter-y-sm">
      <div class="row items-center no-wrap q-gutter-sm">
        <div class="col text-caption app-muted ellipsis">{{ scopeDescription }}</div>
        <q-btn
          v-if="workspace.toolContext"
          flat
          dense
          no-caps
          color="primary"
          icon="undo"
          label="Zu aktuellen Dateien"
          @click="workspace.returnToCurrentContext()"
        />
      </div>
      <div class="row items-center no-wrap">
        <q-btn
          class="file-icon-action q-mr-xs"
          flat
          round
          dense
          icon="arrow_upward"
          aria-label="Zum übergeordneten Ordner"
          :disable="!explorerAvailable || workspace.directoryPath === browserRootPath"
          @click="navigateDirectory(selectedParentPath)"
        >
          <q-tooltip>Zum übergeordneten Ordner</q-tooltip>
        </q-btn>
        <q-breadcrumbs class="col file-breadcrumbs" separator="/" active-color="primary">
          <q-breadcrumbs-el
            v-for="crumb in selectedBreadcrumbs"
            :key="crumb.path"
            :label="crumb.label"
            :class="{ 'cursor-pointer': explorerAvailable && crumb.path !== workspace.directoryPath }"
            @click="explorerAvailable && crumb.path !== workspace.directoryPath && navigateDirectory(crumb.path)"
          />
        </q-breadcrumbs>
      </div>
      <q-input v-model="workspace.filter" outlined dense clearable label="Geladene Einträge filtern" :disable="!explorerAvailable">
        <template #prepend><q-icon name="search" /></template>
      </q-input>
      <div class="file-explorer__actions row items-center no-wrap q-gutter-sm">
        <q-btn-dropdown v-if="canCreateFile || canCreateDirectory" unelevated no-caps color="primary" icon="add" label="Neu">
          <q-list padding class="file-actions-menu">
            <q-item v-if="canCreateFile" clickable v-ripple v-close-popup @click="openCreateDialog('file')">
              <q-item-section avatar><q-icon name="note_add" /></q-item-section>
              <q-item-section>Neue Datei</q-item-section>
            </q-item>
            <q-item v-if="canCreateDirectory" clickable v-ripple v-close-popup @click="openCreateDialog('directory')">
              <q-item-section avatar><q-icon name="create_new_folder" /></q-item-section>
              <q-item-section>Neuer Ordner</q-item-section>
            </q-item>
          </q-list>
        </q-btn-dropdown>
        <q-btn
          v-if="canUpload"
          class="file-upload-action"
          outline
          no-caps
          color="primary"
          icon="upload_file"
          label="Hochladen"
          aria-label="Dateien hochladen"
          :loading="workspace.uploading"
          @click="uploadInput.click()"
        >
          <q-tooltip>Dateien hochladen</q-tooltip>
        </q-btn>
      </div>
      <input ref="uploadInput" type="file" multiple class="hidden" @change="uploadSelected">
    </div>
    <q-separator />
    <q-linear-progress
      v-if="workspace.loading[workspace.directoryPath]"
      indeterminate
      color="primary"
      aria-label="Dateien werden geladen"
    />

    <q-scroll-area class="col">
      <div
        v-if="explorerUnavailableMessage"
        class="file-load-state app-muted q-ma-md q-pa-lg text-center"
        role="status"
      >
        <q-icon :name="explorerUnavailableIcon" size="40px" />
        <div class="q-mt-sm">{{ explorerUnavailableMessage }}</div>
      </div>
      <div
        v-else
        class="q-pa-sm"
        role="tree"
        aria-label="Dateien und Ordner"
        @keydown="handleTreeKeydown"
      >
        <div
          :class="['file-tree-row row items-center no-wrap', { 'file-tree-row--selected': workspace.selectedPath === workspace.directoryPath }]"
          role="treeitem"
          :tabindex="treeFocusPath === workspace.directoryPath ? 0 : -1"
          :data-file-path="workspace.directoryPath"
          data-file-kind="directory"
          aria-level="1"
          aria-expanded="true"
          :aria-selected="workspace.selectedPath === workspace.directoryPath"
          :aria-current="workspace.selectedPath === workspace.directoryPath ? 'true' : undefined"
          @click="workspace.selectPath(workspace.directoryPath)"
          @focus="treeFocusPath = workspace.directoryPath"
        >
          <span class="file-tree-spacer" />
          <q-icon name="folder_special" size="20px" />
          <span class="q-ml-sm">{{ workspace.directoryPath }}</span>
        </div>
        <div v-if="workspace.loadErrors[workspace.directoryPath]" class="file-load-state q-ma-sm q-pa-md text-center" role="alert">
          <q-icon name="cloud_off" size="32px" class="app-muted" />
          <div class="q-mt-sm">{{ rootLoadError }}</div>
          <q-btn class="q-mt-sm" flat no-caps color="primary" label="Erneut versuchen" @click="workspace.load(workspace.directoryPath)" />
        </div>
        <div v-if="workspace.loading[workspace.directoryPath] && !workspace.entries[workspace.directoryPath]" class="q-pa-lg text-center">
          <q-spinner color="primary" size="32px" />
          <div class="app-muted q-mt-sm">Dateien werden geladen ...</div>
        </div>
        <div v-else-if="filterEmpty" class="file-load-state app-muted q-ma-sm q-pa-md text-center">
          <q-icon name="search_off" size="32px" />
          <div class="q-mt-sm">Keine Dateien oder Ordner entsprechen dem Filter.</div>
        </div>
        <div
          v-else-if="workspace.entries[workspace.directoryPath] && !workspace.entries[workspace.directoryPath].length && !workspace.loadErrors[workspace.directoryPath]"
          class="file-load-state app-muted q-ma-sm q-pa-md text-center"
        >
          <q-icon name="folder_open" size="32px" />
          <div class="q-mt-sm">Hier sind noch keine Dateien oder Ordner.</div>
        </div>
        <FileTreeNode
          v-for="entry in workspace.entries[workspace.directoryPath] || []"
          :key="`${workspace.contextVersion}:${entry.path}`"
          :entry="entry"
          :filter="workspace.filter || ''"
          :focus-path="treeFocusPath"
          @open-file="openViewer"
          @focus-entry="treeFocusPath = $event"
          @download-entry="workspace.download($event.path)"
          @rename-entry="openRenameDialog"
          @delete-entry="removeEntry"
        />
        <q-btn
          v-if="workspace.hasMore[workspace.directoryPath]"
          flat
          dense
          no-caps
          color="primary"
          label="Weitere laden"
          class="q-ml-xl"
          :disable="workspace.loading[workspace.directoryPath]"
          @click="workspace.load(workspace.directoryPath, true)"
        />
      </div>
    </q-scroll-area>
    </div>

    <q-dialog v-model="renameDialogOpen" :persistent="renaming">
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
              lazy-rules
              :rules="[requiredRule]"
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

    <q-dialog v-model="createDialogOpen" :persistent="creating">
      <q-card class="file-create-dialog">
        <q-form @submit.prevent="createFile">
          <q-card-section>
            <div class="text-h6">{{ createKind === 'directory' ? 'Neuer Ordner' : 'Neue Datei' }}</div>
            <div class="text-caption app-muted q-mt-xs ellipsis">{{ mutationDirectory }}</div>
          </q-card-section>
          <q-card-section class="q-pt-none q-gutter-y-md">
            <q-select
              v-if="createKind === 'file'"
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
              :label="createKind === 'directory' ? 'Ordnername' : 'Dateiname'"
              maxlength="240"
              lazy-rules
              :rules="[requiredRule]"
              :hint="createKind === 'file' ? (createName.trim() ? `Wird als „${createFileName}“ angelegt.` : 'Die Endung wird automatisch ergänzt.') : undefined"
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

    <q-dialog v-model="permissionsDialogOpen" :persistent="permissionLoading || Boolean(revokingId)">
      <q-card class="file-permissions-dialog">
        <q-toolbar>
          <q-icon name="verified_user" color="primary" size="24px" class="q-ml-sm" />
          <q-toolbar-title class="text-subtitle1 text-weight-bold">Session-Freigaben</q-toolbar-title>
          <q-btn class="file-icon-action" flat round dense icon="close" aria-label="Dialog schließen" :disable="Boolean(revokingId)" v-close-popup>
            <q-tooltip>Schließen</q-tooltip>
          </q-btn>
        </q-toolbar>
        <q-separator />
        <q-card-section class="q-pa-none file-permissions-dialog__content">
          <div v-if="permissionLoading" class="q-pa-xl text-center"><q-spinner color="primary" /></div>
          <div v-else-if="permissionError" class="file-load-state q-ma-md q-pa-md text-center" role="alert">
            <q-icon name="cloud_off" size="32px" class="app-muted" />
            <div class="q-mt-sm">{{ permissionErrorMessage }}</div>
            <q-btn class="q-mt-sm" flat no-caps color="primary" label="Erneut versuchen" @click="loadPermissions" />
          </div>
          <q-list v-else separator>
            <q-item v-for="grant in permissions" :key="grant.id">
              <q-item-section avatar>
                <q-icon :name="grantDisplay(grant).icon" color="primary" />
              </q-item-section>
              <q-item-section>
                <q-item-label>{{ grantDisplay(grant).label }}</q-item-label>
                <q-item-label caption class="file-path-value">{{ grantDisplay(grant).value }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn
                  class="file-icon-action"
                  flat
                  round
                  dense
                  icon="delete_outline"
                  color="negative"
                  :loading="revokingId === grant.id"
                  :disable="Boolean(revokingId)"
                  :aria-label="`${grantDisplay(grant).label} widerrufen`"
                  @click="revoke(grant)"
                >
                  <q-tooltip>Freigabe widerrufen</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>
            <q-item v-if="!permissions.length"><q-item-section class="app-muted">Keine Freigaben vorhanden.</q-item-section></q-item>
          </q-list>
        </q-card-section>
      </q-card>
    </q-dialog>

    <q-dialog v-model="viewerFullscreen" maximized transition-show="fade" transition-hide="fade">
      <q-card class="app-file-dialog">
        <FileViewerPanel
          v-if="workspace.viewedEntry"
          :entry="workspace.viewedEntry"
          fullscreen
          fullscreen-toggle
          @back="backToFiles"
          @close="closeExplorer"
          @navigate="navigateFromViewer"
          @toggle-fullscreen="viewerFullscreen = false"
        />
      </q-card>
    </q-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import ControlCenterContentHeader from 'components/ControlCenterContentHeader.vue'
import FileTreeNode from 'components/FileTreeNode.vue'
import FileViewerPanel from 'components/FileViewerPanel.vue'
import { useConfirm } from 'src/composables/confirm'
import { permissionGrantDisplay } from 'src/utils/approval-display'
import { apiErrorMessage } from 'src/utils/api-errors'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useInstanceLimitsStore } from 'stores/instance-limits'
import {
  MAX_WORKSPACE_UPLOADS,
  parentPath,
  workspaceContextKey,
  withFileSuffix,
  useWorkspaceStore
} from 'stores/workspace'

defineOptions({ name: 'FileExplorerPanel' })
const emit = defineEmits(['close', 'remove', 'move'])

const $q = useQuasar()
const { confirm } = useConfirm()
const workspace = useWorkspaceStore()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const executionAgents = useExecutionAgentStore()
const instanceLimits = useInstanceLimitsStore()
const approvals = useApprovalStore()
const permissions = ref([])
const permissionLoading = ref(false)
const permissionError = ref(null)
const permissionsDialogOpen = ref(false)
const revokingId = ref(null)
const uploadInput = ref(null)
const explorerRoot = ref(null)
const treeFocusPath = ref('.')
const renameDialogOpen = ref(false)
const renameTarget = ref(null)
const renameName = ref('')
const renaming = ref(false)
const createDialogOpen = ref(false)
const createKind = ref('file')
const createName = ref('')
const createSuffix = ref('.txt')
const creating = ref(false)
const viewerFullscreen = ref(false)
let typeahead = ''
let typeaheadTimer = null
const fileTypeOptions = [
  { label: 'Textdatei (.txt)', value: '.txt' },
  { label: 'Markdown (.md)', value: '.md' },
  { label: 'OpenSCAD (.scad)', value: '.scad' }
]
const createFileName = computed(() => withFileSuffix(createName.value, createSuffix.value))
const integrationIndex = computed(() => controlCenter.tabs.findIndex((tab) => tab.appId === 'files'))
const selectedIsDirectory = computed(() => workspace.selectedKind === 'directory')
const selectedExecutionAgent = computed(() => (
  executionAgents.byId(chat.activeContext?.preferredExecutionAgentId)
))
const managedSourceLabel = computed(() => 'Ablage')
const hasOverflowActions = computed(() => Boolean(
  (workspace.scope === 'filesystem' && explorerAvailable.value)
  || chat.current
))
const targetDirectory = computed(() => {
  if (selectedIsDirectory.value) return workspace.selectedPath
  return parentPath(workspace.selectedPath)
})
const mutationDirectory = computed(() => targetDirectory.value)
const writableLocation = computed(() => !workspace.toolContext)
const canCreateFile = computed(() => writableLocation.value && workspace.operationAvailable('filesystem.import'))
const canCreateDirectory = computed(() => writableLocation.value && workspace.operationAvailable('filesystem.mkdir'))
const canUpload = computed(() => writableLocation.value && workspace.operationAvailable('filesystem.import'))
const canRename = computed(() => Boolean(
  !renaming.value
  && renameName.value.trim()
  && renameTarget.value
  && renameName.value.trim() !== renameTarget.value.name
))
const explorerAvailable = computed(() => workspace.operationAvailable('filesystem.list'))
const explorerUnavailableMessage = computed(() => {
  if (explorerAvailable.value) return ''
  if (workspace.scope === 'session') {
    return `${managedSourceLabel.value} sind verfügbar, sobald die Session erstellt wurde.`
  }
  if (!chat.activeContext?.preferredExecutionAgentId) {
    return workspace.sessionId
      ? 'Wähle einen Computer aus, um Dateien anzuzeigen.'
      : 'Erstelle zuerst eine Session oder wähle einen Computer aus, um Dateien anzuzeigen.'
  }
  if (!selectedExecutionAgent.value) {
    return 'Der ausgewählte Computer ist nicht mehr verfügbar.'
  }
  if (!selectedExecutionAgent.value.connected) {
    return 'Der ausgewählte Computer ist offline.'
  }
  return 'Der ausgewählte Computer bietet keinen Dateizugriff an.'
})
const explorerUnavailableIcon = computed(() => (
  selectedExecutionAgent.value?.connected === false ? 'cloud_off' : 'folder_off'
))
const scopeDescription = computed(() => {
  if (workspace.toolContext) return 'Gespeicherter Dateistand · nur Lesen'
  if (workspace.scope === 'session') return managedSourceLabel.value
  if (!workspace.operationAvailable('filesystem.list')) return 'Der ausgewählte Computer ist nicht verfügbar.'
  return `Aktueller Ordner · ${chat.activeContext?.currentDirectory || selectedExecutionAgent.value?.homeDirectory || '/'}`
})
const browserRootPath = computed(() => workspace.scope === 'filesystem' ? '/' : '.')
const selectedParentPath = computed(() => parentPath(workspace.directoryPath))
const selectedBreadcrumbs = computed(() => workspace.scope === 'filesystem'
  ? [
      { label: '/', path: '/' },
      ...workspace.directoryPath.split('/').filter(Boolean).map((label, index, parts) => ({
        label,
        path: `/${parts.slice(0, index + 1).join('/')}`
      }))
    ]
  : [{ label: managedSourceLabel.value, path: '.' }])
const filterEmpty = computed(() => Boolean(
  workspace.filter
  && workspace.entries[workspace.directoryPath]
  && !Object.values(workspace.entries).flat().some((entry) => (
    entry.name?.toLocaleLowerCase().includes(workspace.filter.toLocaleLowerCase())
  ))
))
const rootLoadError = computed(() => apiErrorMessage(
  workspace.loadErrors[workspace.directoryPath],
  'Die Dateien konnten nicht geladen werden.'
))
const permissionErrorMessage = computed(() => apiErrorMessage(
  permissionError.value,
  'Die Session-Freigaben konnten nicht geladen werden.'
))
const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Name erforderlich.'
const activeWorkspaceContext = computed(() => executionAgents.workspaceContextFor(chat.activeContext))

function grantDisplay(grant) {
  return permissionGrantDisplay(grant, chat.cachedSessions)
}

function refreshFiles() {
  const requests = [workspace.refresh()]
  if (chat.current?.id) requests.push(workspace.fetchStorageUsage(chat.current.id))
  return Promise.all(requests)
}

async function loadPermissions() {
  permissionLoading.value = true
  permissionError.value = null
  try {
    permissions.value = chat.current
      ? await approvals.permissions(chat.current.id, { suppressErrorToast: true })
      : []
  } catch (error) {
    permissionError.value = error
  } finally {
    permissionLoading.value = false
  }
}

function openPermissionsDialog() {
  permissionsDialogOpen.value = true
  loadPermissions()
}

function openCreateDialog(kind) {
  createKind.value = kind
  createName.value = ''
  createSuffix.value = '.txt'
  createDialogOpen.value = true
}

async function createFile() {
  if (!createName.value.trim() || creating.value) return
  creating.value = true
  try {
    if (createKind.value === 'directory') {
      await workspace.createDirectory(mutationDirectory.value, createName.value.trim())
    } else {
      await workspace.createFile(mutationDirectory.value, createFileName.value)
    }
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
  const oversized = files.find((file) => file.size > instanceLimits.maxUploadBytes)
  if (oversized) {
    $q.notify({ type: 'negative', message: `„${oversized.name}“ ist größer als ${instanceLimits.maxUploadMib} MiB.` })
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

function visibleTreeItems() {
  return [...(explorerRoot.value?.querySelectorAll('[role="treeitem"]') || [])]
}

function focusTreeItem(item) {
  if (!item) return
  treeFocusPath.value = item.dataset.filePath
  item.focus()
}

function handleTreeKeydown(event) {
  if (event.altKey || event.ctrlKey || event.metaKey || event.target.closest('button')) return
  const current = event.target.closest('[role="treeitem"]')
  if (!current) return
  const items = visibleTreeItems()
  const index = items.indexOf(current)
  let target = null
  if (event.key === 'ArrowDown') target = items[index + 1] || current
  else if (event.key === 'ArrowUp') target = items[index - 1] || current
  else if (event.key === 'Home') target = items[0]
  else if (event.key === 'End') target = items.at(-1)
  else if (event.key === 'ArrowRight' && current.dataset.fileKind === 'directory') {
    if (current.getAttribute('aria-expanded') === 'false') current.click()
    else target = items.slice(index + 1).find((item) => item.dataset.filePath.startsWith(
      current.dataset.filePath === '.' ? '' : current.dataset.filePath === '/' ? '/' : `${current.dataset.filePath}/`
    ))
  } else if (event.key === 'ArrowLeft') {
    if (current.getAttribute('aria-expanded') === 'true' && current.dataset.filePath !== '.') current.click()
    else target = items.find((item) => item.dataset.filePath === parentPath(current.dataset.filePath))
  } else if (event.key === 'Enter' || event.key === ' ') {
    current.click()
  } else if (event.key.length === 1 && /\S/u.test(event.key)) {
    clearTimeout(typeaheadTimer)
    typeahead += event.key.toLocaleLowerCase()
    typeaheadTimer = setTimeout(() => { typeahead = '' }, 700)
    target = [...items.slice(index + 1), ...items.slice(0, index + 1)].find((item) => (
      item.textContent.trim().toLocaleLowerCase().startsWith(typeahead)
    ))
  } else return
  event.preventDefault()
  focusTreeItem(target)
}

function navigateDirectory(path) {
  workspace.openDirectory(path)
  treeFocusPath.value = path
  focusPath(path)
}

async function navigateFromViewer(path) {
  if (!await confirmDiscard()) return
  viewerFullscreen.value = false
  workspace.closeViewer()
  navigateDirectory(path)
}

async function backToFiles() {
  if (!await confirmDiscard()) return
  viewerFullscreen.value = false
  const path = workspace.viewedEntry?.path
  workspace.closeViewer()
  await nextTick()
  const rows = [...(explorerRoot.value?.querySelectorAll('[data-file-path]') || [])]
  const row = rows.find((item) => item.dataset.filePath === path)
    || rows.find((item) => item.dataset.filePath === workspace.directoryPath)
  row?.focus()
}

async function focusPath(path, fallback = workspace.directoryPath) {
  await nextTick()
  const rows = [...(explorerRoot.value?.querySelectorAll('[data-file-path]') || [])]
  const row = rows.find((item) => item.dataset.filePath === path)
    || rows.find((item) => item.dataset.filePath === fallback)
    || rows.find((item) => item.dataset.filePath === workspace.directoryPath)
  row?.focus()
}

async function closeExplorer() {
  if (!await confirmDiscard()) return
  viewerFullscreen.value = false
  emit('close')
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
  await chat.updateActive({ current_directory: workspace.selectedPath })
  $q.notify({ type: 'positive', message: 'Aktueller Ordner wurde geändert.' })
}

function openAbsolutePath() {
  $q.dialog({
    title: 'Absoluten Pfad öffnen',
    prompt: {
      model: workspace.directoryPath,
      type: 'text',
      label: 'Absoluter Pfad',
      isValid: (value) => String(value || '').trim().startsWith('/')
    },
    cancel: { label: 'Abbrechen', flat: true, noCaps: true },
    ok: { label: 'Öffnen', unelevated: true, noCaps: true, color: 'primary' }
  }).onOk((path) => workspace.openDirectory(path.trim()))
}

function openRenameDialog(entry) {
  workspace.selectPath(entry.path, entry.kind)
  renameTarget.value = entry
  renameName.value = entry.name
  renameDialogOpen.value = true
}

async function renameEntry() {
  if (!canRename.value) return
  renaming.value = true
  try {
    const renamed = await workspace.renameEntry(renameTarget.value.path, renameName.value.trim())
    renameDialogOpen.value = false
    if (permissionsDialogOpen.value) await loadPermissions()
    await focusPath(renamed.path, targetDirectory.value)
  } catch {
    // The central API interceptor displays the error; keep the dialog open.
  } finally {
    renaming.value = false
  }
}

async function removeEntry(entry) {
  workspace.selectPath(entry.path, entry.kind)
  const accepted = await confirm(
    entry.kind === 'directory'
      ? `Soll der Ordner „${entry.name}“ einschließlich aller enthaltenen Dateien und Ordner gelöscht werden?`
      : `Soll die Datei „${entry.name}“ gelöscht werden?`,
    entry.kind === 'directory' ? 'Ordner löschen' : 'Datei löschen',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  const parent = parentPath(entry.path)
  await workspace.deleteEntry(entry.path)
  workspace.selectPath(parent)
  if (permissionsDialogOpen.value) await loadPermissions()
  await focusPath(parent)
}

async function revoke(grant) {
  const display = grantDisplay(grant)
  const accepted = await confirm(
    `Soll die Freigabe „${display.label}“ für ${display.value} widerrufen werden?`,
    'Freigabe widerrufen',
    { okLabel: 'Widerrufen', okColor: 'negative' }
  )
  if (!accepted) return
  revokingId.value = grant.id
  try {
    await approvals.revoke(chat.current.id, grant.id)
    await loadPermissions()
  } finally {
    revokingId.value = null
  }
}

watch(
  [
    () => chat.selectedSessionId || null,
    () => chat.current?.id || null,
    () => chat.current?.projectId || null,
    () => chat.current?.currentDirectory || null,
    () => chat.activeContext?.currentDirectory || null,
    () => workspaceContextKey(activeWorkspaceContext.value),
    () => controlCenter.requestGeneration,
    () => controlCenter.ready
  ],
  ([selectedSessionId, currentSessionId, currentProjectId, currentDirectory, draftCurrentDirectory]) => {
    const sessionId = selectedSessionId || currentSessionId
    const selectedDirectory = currentSessionId === sessionId
      ? currentDirectory
      : sessionId ? null : draftCurrentDirectory
    workspace.configure({
      sessionId,
      rootIdentity: sessionId ? currentProjectId || sessionId : null,
      currentDirectory: selectedDirectory || selectedExecutionAgent.value?.homeDirectory || '/',
      workspaceContext: activeWorkspaceContext.value
    })
    if (
      sessionId
      && currentSessionId === sessionId
      && controlCenter.sessionId === sessionId
      && controlCenter.ready
    ) workspace.restoreView(controlCenter.fileView)
    workspace.load(workspace.directoryPath)
    if (permissionsDialogOpen.value) loadPermissions()
  },
  { immediate: true }
)

watch(() => workspace.contextVersion, () => { treeFocusPath.value = workspace.directoryPath })
watch(() => workspace.viewedEntry, (entry) => {
  if (!entry) viewerFullscreen.value = false
})
watch(() => workspace.filter, async () => {
  await nextTick()
  if (!visibleTreeItems().some((item) => item.dataset.filePath === treeFocusPath.value)) {
    treeFocusPath.value = workspace.directoryPath
  }
})
</script>
