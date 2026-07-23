<template>
  <div class="column full-height no-wrap file-explorer">
    <div class="row items-center no-wrap q-pa-md q-gutter-sm">
      <q-icon name="folder_open" color="primary" size="26px" />
      <div class="text-subtitle1 text-weight-bold">Agent-Dateien</div>
      <q-space />
      <q-btn flat round dense icon="refresh" aria-label="Aktualisieren" @click="workspace.load('.')" />
      <q-btn flat round dense icon="close" aria-label="Explorer schließen" @click="workspace.open = false" />
    </div>
    <div class="q-px-md q-pb-md">
      <q-input v-model="workspace.filter" outlined dense clearable placeholder="Dateien filtern">
        <template #prepend><q-icon name="search" /></template>
      </q-input>
    </div>
    <q-separator />

    <q-scroll-area class="col">
      <div class="q-pa-sm">
        <div
          :class="['file-tree-row row items-center no-wrap', { 'file-tree-row--selected': workspace.selectedPath === '.' }]"
          @click="workspace.selectedPath = '.'"
        >
          <span class="file-tree-spacer" />
          <q-icon name="home" size="20px" />
          <span class="q-ml-sm">home</span>
          <q-space />
          <q-icon v-if="chat.activeContext?.workingDirectory === '.'" name="home_work" color="primary" size="18px" />
        </div>
        <FileTreeNode
          v-for="entry in workspace.entries['.'] || []"
          :key="entry.path"
          :entry="entry"
          :filter="workspace.filter || ''"
          :workdir="chat.activeContext?.workingDirectory"
        />
        <div v-if="workspace.loading['.']" class="q-pa-lg text-center"><q-spinner color="primary" /></div>
      </div>
    </q-scroll-area>

    <q-separator />
    <div class="q-pa-md q-gutter-sm">
      <div class="text-caption app-muted ellipsis">Ausgewählt: {{ workspace.selectedPath }}</div>
      <div class="row q-gutter-sm">
        <q-btn outline no-caps color="primary" icon="create_new_folder" label="Ordner" @click="createFolder" />
        <q-btn
          v-if="selectedIsDirectory"
          outline
          no-caps
          color="primary"
          icon="home_work"
          label="Als Workdir"
          @click="setWorkdir"
        />
        <q-btn
          v-if="chat.activeContext?.workingDirectory"
          flat
          no-caps
          color="secondary"
          icon="link_off"
          label="Workdir lösen"
          @click="chat.updateActive({ working_directory: null })"
        />
        <q-space />
        <q-btn
          v-if="workspace.selectedPath !== '.'"
          flat
          round
          color="negative"
          icon="delete"
          aria-label="Löschen"
          @click="removeSelected"
        />
      </div>

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
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import FileTreeNode from 'components/FileTreeNode.vue'
import { useConfirm } from 'src/composables/confirm'
import { permissionGrantDisplay } from 'src/utils/approval-display'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileExplorerPanel' })

const $q = useQuasar()
const { confirm } = useConfirm()
const workspace = useWorkspaceStore()
const chat = useChatStore()
const approvals = useApprovalStore()
const permissions = ref([])
const selectedEntry = computed(() => {
  if (workspace.selectedPath === '.') return { kind: 'directory' }
  return Object.values(workspace.entries).flat().find((item) => item.path === workspace.selectedPath)
})
const selectedIsDirectory = computed(() => selectedEntry.value?.kind === 'directory')

function grantDisplay(grant) {
  return permissionGrantDisplay(grant, chat.sessions)
}

async function loadPermissions() {
  permissions.value = chat.current ? await approvals.permissions(chat.current.id) : []
}

function createFolder() {
  const parent = selectedIsDirectory.value ? workspace.selectedPath : workspace.selectedPath.includes('/') ? workspace.selectedPath.slice(0, workspace.selectedPath.lastIndexOf('/')) : '.'
  $q.dialog({
    title: 'Neuer Ordner',
    prompt: { model: '', type: 'text', label: 'Ordnername', outlined: true },
    cancel: true,
    ok: { label: 'Anlegen', color: 'primary' }
  }).onOk((name) => workspace.createDirectory(parent || '.', name))
}

async function setWorkdir() {
  await chat.updateActive({ working_directory: workspace.selectedPath })
}

async function removeSelected() {
  const accepted = await confirm(
    `Soll „${workspace.selectedPath}“ einschließlich aller enthaltenen Dateien und Ordner gelöscht werden?`,
    'Pfad löschen',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  await workspace.deleteEntry(workspace.selectedPath)
  workspace.selectedPath = '.'
  await loadPermissions()
}

async function revoke(id) {
  await approvals.revoke(chat.current.id, id)
  await loadPermissions()
}

watch(() => chat.current?.id, loadPermissions)
onMounted(() => {
  workspace.load('.')
  loadPermissions()
})
</script>
