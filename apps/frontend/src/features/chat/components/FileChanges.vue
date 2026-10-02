<template>
  <div v-if="changes.length" class="file-changes q-mt-xs">
    <div class="app-muted">Dateiänderungen: {{ changes.length }}</div>
    <q-list>
      <q-item
        v-for="change in changes"
        :key="change.key"
        dense
        class="file-changes__item q-px-xs q-py-none"
        :clickable="!readOnly && change.kind === 'file' && change.operation !== 'delete'"
        @click="open(change)"
      >
        <q-item-section side class="q-pr-xs">
          <q-icon :name="change.kind === 'directory' ? 'folder' : attachmentIcon(change)" size="12px" />
        </q-item-section>
        <q-item-section>
          <q-item-label class="ellipsis" :title="change.path">
            {{ change.filename || change.path }}
          </q-item-label>
        </q-item-section>
        <q-item-section side class="q-pl-xs">
          {{ change.operations.map((operation) => labels[operation]).join(' · ') }}
        </q-item-section>
        <q-item-section v-if="!readOnly && change.kind === 'file' && change.operation !== 'delete'" side class="q-pl-xs">
          <q-icon name="open_in_new" size="12px" />
        </q-item-section>
        <q-tooltip>
          <span v-if="change.sourcePath">{{ change.sourcePath }} → </span>{{ change.path }}
          · {{ change.root === 'session' ? 'Session-Ablage' : agents.byId(change.executionAgentId)?.name || change.executionAgentId }}
        </q-tooltip>
      </q-item>
    </q-list>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { attachmentIcon } from 'src/utils/attachments'
import { useExecutionAgentStore } from 'src/features/computers/store'

const props = defineProps({ actions: { type: Array, default: () => [] }, readOnly: Boolean })
const emit = defineEmits(['open-file'])
const agents = useExecutionAgentStore()
const labels = { write: 'Geändert', create: 'Erstellt', edit: 'Geändert', delete: 'Gelöscht', mkdir: 'Ordner erstellt', copy: 'Kopiert', move: 'Verschoben' }
const changes = computed(() => {
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
  return [...grouped.values()]
})
function open(change) {
  if (!props.readOnly && change.kind === 'file' && change.operation !== 'delete') emit('open-file', change)
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
