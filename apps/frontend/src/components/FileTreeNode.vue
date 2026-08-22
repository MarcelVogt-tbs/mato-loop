<template>
  <div v-if="visible" class="file-tree-node" role="none">
    <div
      :class="['file-tree-row row items-center no-wrap', { 'file-tree-row--selected': selected }]"
      role="treeitem"
      :tabindex="focusPath === entry.path ? 0 : -1"
      :data-file-path="entry.path"
      :data-file-kind="entry.kind"
      :aria-level="level + 2"
      :aria-selected="selected"
      :aria-current="selected ? 'true' : undefined"
      :aria-expanded="entry.kind === 'directory' ? expanded : undefined"
      @click="activate"
      @focus="emit('focus-entry', entry.path)"
    >
      <div
        class="file-tree-row__main col row items-center no-wrap"
        :style="{ paddingLeft: `${level * 18 + 8}px` }"
      >
        <span class="file-tree-spacer flex flex-center" aria-hidden="true">
          <q-icon
            v-if="entry.kind === 'directory'"
            :name="expanded ? 'expand_more' : 'chevron_right'"
            size="18px"
          />
        </span>
        <q-icon :name="icon" :color="entry.kind === 'symlink' ? 'warning' : undefined" size="20px" />
        <span class="ellipsis q-ml-sm">{{ entry.name }}</span>
      </div>
      <q-btn
        v-if="canRename || canDelete || canDownload"
        flat
        round
        dense
        size="sm"
        icon="more_vert"
        class="file-tree-row__menu"
        :aria-label="`Aktionen für ${entry.name}`"
        @click.stop="selectOnly"
      >
        <q-tooltip>Aktionen</q-tooltip>
        <q-menu anchor="bottom right" self="top right" :offset="[0, 6]" class="app-action-menu">
          <q-list padding class="file-actions-menu">
            <q-item v-if="canDownload" clickable v-ripple v-close-popup @click="emit('download-entry', entry)">
              <q-item-section avatar><q-icon name="download" /></q-item-section>
              <q-item-section>Herunterladen</q-item-section>
            </q-item>
            <q-item v-if="canRename" clickable v-ripple v-close-popup @click="emit('rename-entry', entry)">
              <q-item-section avatar><q-icon name="edit" /></q-item-section>
              <q-item-section>Umbenennen</q-item-section>
            </q-item>
            <q-separator v-if="canDelete && (canDownload || canRename)" class="q-my-xs" />
            <q-item
              v-if="canDelete"
              clickable
              v-ripple
              v-close-popup
              class="text-negative"
              @click="emit('delete-entry', entry)"
            >
              <q-item-section avatar><q-icon name="delete_outline" /></q-item-section>
              <q-item-section>Löschen</q-item-section>
            </q-item>
          </q-list>
        </q-menu>
      </q-btn>
    </div>
    <div v-if="expanded && entry.kind === 'directory'">
      <div v-if="workspace.loadErrors[entry.path]" class="file-inline-state q-ml-xl q-pa-sm" role="alert">
        <span>{{ directoryError }}</span>
        <q-btn flat dense no-caps color="primary" label="Erneut versuchen" @click="workspace.load(entry.path)" />
      </div>
      <div v-if="workspace.loading[entry.path] && !workspace.entries[entry.path]" class="q-pa-sm text-center">
        <q-spinner color="primary" size="20px" />
      </div>
      <div
        v-else-if="workspace.entries[entry.path] && !workspace.entries[entry.path].length && !workspace.loadErrors[entry.path]"
        class="file-inline-state app-muted q-ml-xl q-pa-sm"
      >
        Dieser Ordner ist leer.
      </div>
      <div role="group">
        <FileTreeNode
          v-for="child in workspace.entries[entry.path] || []"
          :key="child.path"
          :entry="child"
          :level="level + 1"
          :filter="filter"
          :focus-path="focusPath"
          @open-file="emit('open-file', $event)"
          @focus-entry="emit('focus-entry', $event)"
          @download-entry="emit('download-entry', $event)"
          @rename-entry="emit('rename-entry', $event)"
          @delete-entry="emit('delete-entry', $event)"
        />
      </div>
      <q-btn
        v-if="workspace.hasMore[entry.path]"
        flat
        dense
        no-caps
        color="primary"
        label="Weitere laden"
        :disable="workspace.loading[entry.path]"
        :style="{ marginLeft: `${(level + 1) * 18 + 40}px` }"
        @click="workspace.load(entry.path, true)"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { apiErrorMessage } from 'src/utils/api-errors'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileTreeNode' })
const props = defineProps({
  entry: { type: Object, required: true },
  level: { type: Number, default: 0 },
  filter: { type: String, default: '' },
  focusPath: { type: String, default: '.' }
})
const emit = defineEmits(['open-file', 'focus-entry', 'download-entry', 'rename-entry', 'delete-entry'])
const workspace = useWorkspaceStore()
const expanded = ref(false)
const selected = computed(() => workspace.selectedPath === props.entry.path)
const mutableEntry = computed(() => (
  !workspace.toolContext
  && ['file', 'directory'].includes(props.entry.kind)
  && (workspace.scope === 'filesystem' || props.entry.path.startsWith('files/'))
))
const canRename = computed(() => mutableEntry.value && workspace.operationAvailable('workspace.rename'))
const canDelete = computed(() => mutableEntry.value && workspace.operationAvailable('workspace.delete'))
const canDownload = computed(() => (
  props.entry.kind === 'file' && workspace.operationAvailable('workspace.export')
))
const visible = computed(() => !props.filter || cachedSubtreeMatches(props.entry) || expanded.value)
const icon = computed(() => ({ directory: expanded.value ? 'folder_open' : 'folder', symlink: 'link', file: 'description' })[props.entry.kind])
const directoryError = computed(() => apiErrorMessage(
  workspace.loadErrors[props.entry.path],
  'Der Ordner konnte nicht geladen werden.'
))

function cachedSubtreeMatches(entry) {
  const filter = props.filter.toLocaleLowerCase()
  return entry.name.toLocaleLowerCase().includes(filter)
    || (entry.kind === 'directory' && (workspace.entries[entry.path] || []).some(cachedSubtreeMatches))
}

function activate() {
  if (props.entry.kind === 'directory') {
    workspace.selectPath(props.entry.path, props.entry.kind)
    toggle()
  }
  else if (props.entry.kind === 'file' && workspace.operationAvailable('workspace.read')) {
    emit('open-file', { entry: props.entry })
  } else workspace.selectPath(props.entry.path, props.entry.kind)
}

function selectOnly() {
  workspace.selectPath(props.entry.path, props.entry.kind)
}

async function toggle() {
  if (props.entry.kind !== 'directory') return
  if (!workspace.operationAvailable('workspace.list')) return
  expanded.value = !expanded.value
  if (expanded.value && !workspace.entries[props.entry.path]) await workspace.load(props.entry.path)
}

</script>
