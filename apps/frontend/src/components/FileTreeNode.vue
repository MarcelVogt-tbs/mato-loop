<template>
  <div v-if="visible" class="file-tree-node">
    <div
      :class="['file-tree-row row items-center no-wrap', { 'file-tree-row--selected': selected }]"
    >
      <div
        class="file-tree-row__main col row items-center no-wrap"
        :style="{ paddingLeft: `${level * 18 + 8}px` }"
        role="button"
        tabindex="0"
        :data-file-path="entry.path"
        :aria-current="selected ? 'true' : undefined"
        :aria-expanded="entry.kind === 'directory' ? expanded : undefined"
        @click="activate"
        @keydown.enter="activate"
        @keydown.space.prevent="activate"
        @keydown.right.prevent="expand"
        @keydown.left.prevent="collapse"
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
        v-if="canMutate"
        flat
        round
        dense
        size="sm"
        icon="more_vert"
        class="file-tree-row__menu"
        :aria-label="`Aktionen für ${entry.name}`"
        @click.stop="selectOnly"
      >
        <q-menu anchor="bottom right" self="top right" :offset="[0, 6]" class="app-action-menu">
          <q-list padding class="file-actions-menu">
            <q-item clickable v-ripple v-close-popup @click="emit('rename-entry', entry)">
              <q-item-section avatar><q-icon name="edit" /></q-item-section>
              <q-item-section>Umbenennen</q-item-section>
            </q-item>
            <q-separator class="q-my-xs" />
            <q-item
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
      <div v-if="workspace.loading[entry.path]" class="q-pa-sm text-center">
        <q-spinner color="primary" size="20px" />
      </div>
      <FileTreeNode
        v-for="child in workspace.entries[entry.path] || []"
        :key="child.path"
        :entry="child"
        :level="level + 1"
        :filter="filter"
        @open-file="emit('open-file', $event)"
        @rename-entry="emit('rename-entry', $event)"
        @delete-entry="emit('delete-entry', $event)"
      />
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
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileTreeNode' })
const props = defineProps({
  entry: { type: Object, required: true },
  level: { type: Number, default: 0 },
  filter: { type: String, default: '' },
})
const emit = defineEmits(['open-file', 'rename-entry', 'delete-entry'])
const workspace = useWorkspaceStore()
const expanded = ref(false)
const selected = computed(() => workspace.selectedPath === props.entry.path)
const canMutate = computed(() => (
  ['file', 'directory'].includes(props.entry.kind)
  && (workspace.scope === 'filesystem' || props.entry.path.startsWith('files/'))
))
const visible = computed(() => !props.filter || props.entry.name.toLowerCase().includes(props.filter.toLowerCase()) || expanded.value)
const icon = computed(() => ({ directory: expanded.value ? 'folder_open' : 'folder', symlink: 'link', file: 'description' })[props.entry.kind])

function activate() {
  workspace.selectedPath = props.entry.path
  if (props.entry.kind === 'directory') toggle()
  else if (props.entry.kind === 'file') emit('open-file', { entry: props.entry })
}

function selectOnly() {
  workspace.selectedPath = props.entry.path
}

async function toggle() {
  if (props.entry.kind !== 'directory') return
  expanded.value = !expanded.value
  if (expanded.value && !workspace.entries[props.entry.path]) await workspace.load(props.entry.path)
}

async function expand() {
  if (props.entry.kind !== 'directory' || expanded.value) return
  await toggle()
}

function collapse() {
  if (props.entry.kind === 'directory' && expanded.value) expanded.value = false
}
</script>
