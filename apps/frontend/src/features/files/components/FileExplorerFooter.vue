<template>
  <div class="file-explorer__footer row items-center no-wrap q-px-md q-py-xs text-caption app-muted">
    <div v-if="chat.current" class="file-explorer__storage col row items-center no-wrap" tabindex="0" :aria-label="storageDescription">
      <q-spinner v-if="workspace.storageUsageLoading && !storage" size="18px" class="q-mr-xs" />
      <q-icon v-else :name="workspace.storageUsageFailed ? 'warning_amber' : 'folder'" size="18px" class="q-mr-xs" />
      <q-linear-progress
        v-if="storageProgress !== null"
        :value="storageProgress"
        size="4px"
        rounded
        class="file-explorer__storage-progress q-mr-sm"
        aria-hidden="true"
      />
      <span class="ellipsis">{{ storageLabel || '–' }}</span>
      <q-tooltip>{{ storageDescription }}</q-tooltip>
      <q-btn
        v-if="workspace.storageUsageFailed && !storage"
        flat
        round
        dense
        size="sm"
        icon="refresh"
        aria-label="Speichernutzung erneut laden"
        @click.stop="workspace.fetchStorageUsage(workspace.rootIdentity)"
      >
        <q-tooltip>Speichernutzung erneut laden</q-tooltip>
      </q-btn>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import {
  formatByteSize,
  formatStorageUsage,
  storageUsageProgress
} from 'src/utils/resource-metrics'
import { useChatStore } from 'src/features/chat/store'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileExplorerFooter' })

const chat = useChatStore()
const workspace = useWorkspaceStore()
const storage = computed(() => (
  workspace.storageUsageSessionId === workspace.rootIdentity ? workspace.storageUsage : null
))
const storageLabel = computed(() => formatStorageUsage(storage.value))
const storageProgress = computed(() => storageUsageProgress(storage.value))
const storageScopeLabel = 'Benutzer-Speicher'
const storageDescription = computed(() => {
  if (workspace.storageUsageLoading && !storage.value) return `${storageScopeLabel}: Speichernutzung wird geladen.`
  if (!storage.value) return `${storageScopeLabel}: Speichernutzung nicht verfügbar.`
  const limit = storage.value.quotaBytes === null ? 'unbegrenzt' : `Limit ${formatByteSize(storage.value.quotaBytes)}`
  const stale = workspace.storageUsageFailed ? ' Letzter Stand.' : ''
  return `${storageScopeLabel}: ${formatByteSize(storage.value.usedBytes)} belegt, ${limit}, über alle eigenen Sessions.${stale}`
})

watch(
  () => workspace.rootIdentity || null,
  (sessionId) => {
    if (
      !sessionId
      || (workspace.storageUsageSessionId === sessionId
        && workspace.storageUsage
        && !workspace.storageUsageFailed)
    ) return
    void workspace.fetchStorageUsage(sessionId)
  },
  { immediate: true }
)
</script>
