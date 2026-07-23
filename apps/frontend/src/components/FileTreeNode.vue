<template>
  <div v-if="visible" class="file-tree-node">
    <div
      :class="['file-tree-row row items-center no-wrap', { 'file-tree-row--selected': selected }]"
      :style="{ paddingLeft: `${level * 18 + 8}px` }"
      @click="select"
      @dblclick="toggle"
    >
      <q-btn
        v-if="entry.kind === 'directory'"
        flat
        round
        dense
        size="sm"
        :icon="expanded ? 'expand_more' : 'chevron_right'"
        @click.stop="toggle"
      />
      <span v-else class="file-tree-spacer" />
      <q-icon :name="icon" :color="entry.kind === 'symlink' ? 'warning' : undefined" size="20px" />
      <span class="ellipsis q-ml-sm">{{ entry.name }}</span>
      <q-space />
      <q-icon v-if="isWorkdir" name="home_work" color="primary" size="18px">
        <q-tooltip>Aktuelles Workdir</q-tooltip>
      </q-icon>
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
        :workdir="workdir"
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
  workdir: { type: String, default: null }
})
const workspace = useWorkspaceStore()
const expanded = ref(false)
const selected = computed(() => workspace.selectedPath === props.entry.path)
const isWorkdir = computed(() => props.workdir === props.entry.path)
const visible = computed(() => !props.filter || props.entry.name.toLowerCase().includes(props.filter.toLowerCase()) || expanded.value)
const icon = computed(() => ({ directory: expanded.value ? 'folder_open' : 'folder', symlink: 'link', file: 'description' })[props.entry.kind])

function select() {
  workspace.selectedPath = props.entry.path
}

async function toggle() {
  if (props.entry.kind !== 'directory') return
  expanded.value = !expanded.value
  if (expanded.value && !workspace.entries[props.entry.path]) await workspace.load(props.entry.path)
}
</script>
