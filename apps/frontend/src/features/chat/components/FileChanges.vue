<template>
  <div v-if="changes.length" :class="compact ? 'file-changes q-mt-md' : 'q-mt-xs'">
    <div class="row items-center app-muted q-px-xs">
      <span>{{ gitFiles ? 'Geänderte Dateien' : 'Dateiänderungen' }}: {{ changes.length }}</span>
      <template v-if="hasStats">
        <span class="text-positive q-ml-sm">+{{ additions ?? '–' }}</span>
        <span class="text-negative q-ml-xs">−{{ deletions ?? '–' }}</span>
        <q-btn v-if="git && !readOnly" flat round dense size="xs" icon="difference" class="q-ml-xs" aria-label="Aktuellen Git-Gesamtdiff öffnen" @click="emit('open-diff', null)">
          <q-tooltip>Aktuellen Git-Gesamtdiff öffnen</q-tooltip>
        </q-btn>
      </template>
    </div>
    <q-list>
      <q-item
        v-for="change in visibleChanges"
        :key="change.key"
        dense
        class="q-px-xs q-py-none"
        :class="{ 'file-changes__item': compact }"
        :clickable="canOpen(change)"
        :aria-label="canOpen(change) ? `${change.diff ? 'Diff' : 'Datei'} öffnen: ${change.path}` : undefined"
        @click="open(change)"
      >
        <q-item-section side class="q-pr-xs">
          <q-icon :name="change.kind === 'directory' ? 'folder' : attachmentIcon(change)" size="12px" />
        </q-item-section>
        <q-item-section>
          <q-item-label class="ellipsis" :title="change.path">
            {{ compact ? change.filename || change.path : change.path }}
          </q-item-label>
        </q-item-section>
        <q-item-section side class="q-pl-xs">
          <div v-if="change.diff" class="row no-wrap q-gutter-xs">
            <template v-if="change.additions != null">
              <span class="text-positive">+{{ change.additions }}</span>
              <span class="text-negative">−{{ change.deletions }}</span>
            </template>
            <span v-else>–</span>
          </div>
          <span v-else>{{ change.operations.map((operation) => labels[operation]).join(' · ') }}</span>
        </q-item-section>
        <q-item-section v-if="canOpen(change)" side class="q-pl-xs">
          <q-icon name="open_in_new" size="12px" />
        </q-item-section>
        <q-tooltip v-if="!gitFiles">
          <span v-if="change.sourcePath">{{ change.sourcePath }} → </span>{{ change.path }}
          · {{ change.root === 'session' ? 'Session-Ablage' : agents.byId(change.executionAgentId)?.name || change.executionAgentId }}
        </q-tooltip>
      </q-item>
    </q-list>
    <q-btn
      v-if="compact && changes.length > 10"
      flat dense no-caps size="sm" class="app-muted"
      :label="expanded ? 'Weniger anzeigen' : `Alle ${changes.length} anzeigen`"
      :aria-expanded="expanded"
      @click="toggleExpanded"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { attachmentIcon } from 'src/utils/attachments'
import { useExecutionAgentStore } from 'src/features/computers/store'

const props = defineProps({
  actions: { type: Array, default: () => [] }, readOnly: Boolean,
  compact: { type: Boolean, default: true },
  git: { type: Object, default: null },
  gitFiles: { type: Array, default: null }
})
const emit = defineEmits(['open-file', 'open-diff', 'resize-start', 'resized'])
const agents = useExecutionAgentStore()
const expanded = ref(false)
const labels = { write: 'Geändert', create: 'Erstellt', edit: 'Geändert', delete: 'Gelöscht', mkdir: 'Ordner erstellt', copy: 'Kopiert', move: 'Verschoben' }
const changes = computed(() => {
  if (props.gitFiles) return props.gitFiles.map((file) => ({
    ...file, key: file.path, kind: 'file', diff: true, operations: [], operation: file.status === 'deleted' ? 'delete' : 'edit'
  }))
  const grouped = new Map()
  for (const action of props.actions) {
    const key = JSON.stringify([action.root, action.rootIdentity || action.executionAgentId, action.path])
    const previous = grouped.get(key)
    grouped.set(key, { ...previous, ...action, kind: action.kind || 'file', key, operations: [...new Set([...(previous?.operations || []), action.operation])] })
    if (action.operation === 'move') {
      const sourceKey = JSON.stringify([action.root, action.rootIdentity || action.executionAgentId, action.sourcePath])
      const source = grouped.get(sourceKey)
      if (source) grouped.set(sourceKey, { ...source, kind: 'path' })
    }
  }
  return [...grouped.values()].map((change) => {
    const file = props.git?.repository && change.root === 'workspace' && change.executionAgentId === props.git.executionAgentId
      ? props.git.files.find((file) => `${props.git.repository?.replace(/\/$/u, '')}/${file.path}` === change.path)
      : null
    return file ? { ...change, additions: file.additions, deletions: file.deletions, diff: true, diffPath: file.path } : change
  })
})
const visibleChanges = computed(() => props.compact && !expanded.value ? changes.value.slice(0, 10) : changes.value)
const hasStats = computed(() => changes.value.some((change) => change.diff))
const additions = computed(() => changes.value.some((change) => change.diff && change.additions == null) ? null : changes.value.reduce((sum, change) => sum + (change.additions || 0), 0))
const deletions = computed(() => changes.value.some((change) => change.diff && change.deletions == null) ? null : changes.value.reduce((sum, change) => sum + (change.deletions || 0), 0))
function toggleExpanded() {
  emit('resize-start')
  expanded.value = !expanded.value
  nextTick(() => emit('resized'))
}
function canOpen(change) {
  return !props.readOnly && change.kind === 'file' && (change.diff || change.operation !== 'delete')
}
function open(change) {
  if (!canOpen(change)) return
  if (change.diff) emit('open-diff', change.diffPath || change.path)
  else emit('open-file', change)
}
</script>

<style scoped>
.file-changes {
  font-size: 0.625rem;
  line-height: 1.4;
}

.file-changes__item {
  min-height: 24px;
}
</style>
