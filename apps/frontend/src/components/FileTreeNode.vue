<template>
  <div v-if="visible" class="file-tree-node" role="none">
    <div
      :class="['file-tree-row row items-center no-wrap', { 'file-tree-row--selected': selected }]"
      role="treeitem"
      :tabindex="focusPath === entry.path ? 0 : -1"
      :data-file-path="entry.path"
      :data-file-kind="entry.kind"
      aria-level="1"
      :aria-selected="selected"
      :aria-current="selected ? 'true' : undefined"
      @click="activate"
      @focus="emit('focus-entry', entry.path)"
    >
      <div class="file-tree-row__main col row items-center no-wrap q-px-sm">
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
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileTreeNode' })
const props = defineProps({
  entry: { type: Object, required: true },
  filter: { type: String, default: '' },
  focusPath: { type: String, default: '.' },
  mutable: { type: Boolean, default: true }
})
const emit = defineEmits(['open-file', 'open-directory', 'focus-entry', 'download-entry', 'rename-entry', 'delete-entry'])
const workspace = useWorkspaceStore()
const selected = computed(() => workspace.selectedPath === props.entry.path)
const mutableEntry = computed(() => (
  props.mutable
  && !workspace.toolContext
  && ['file', 'directory'].includes(props.entry.kind)
))
const canRename = computed(() => mutableEntry.value && workspace.operationAvailable('filesystem.rename'))
const canDelete = computed(() => mutableEntry.value && workspace.operationAvailable('filesystem.delete'))
const canDownload = computed(() => (
  props.entry.kind === 'file' && workspace.operationAvailable('filesystem.export')
))
const visible = computed(() => !props.filter || props.entry.name.toLocaleLowerCase().includes(props.filter.toLocaleLowerCase()))
const icon = computed(() => ({ directory: 'folder', symlink: 'link', file: 'description' })[props.entry.kind])

function activate() {
  if (props.entry.kind === 'directory') {
    emit('open-directory', props.entry)
  }
  else if (props.entry.kind === 'file' && workspace.operationAvailable('filesystem.read')) {
    emit('open-file', { entry: props.entry })
  } else workspace.selectPath(props.entry.path, props.entry.kind)
}

function selectOnly() {
  workspace.selectPath(props.entry.path, props.entry.kind)
}

</script>
