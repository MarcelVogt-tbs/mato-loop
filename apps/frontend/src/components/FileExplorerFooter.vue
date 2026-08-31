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
        @click.stop="workspace.fetchStorageUsage(chat.current.id)"
      >
        <q-tooltip>Speichernutzung erneut laden</q-tooltip>
      </q-btn>
    </div>
    <q-space v-else />

    <div class="col-auto q-ml-sm">
      <q-btn-dropdown
        flat
        dense
        no-caps
        no-wrap
        auto-close
        color="primary"
        :icon="sourceIcon"
        :label="sourceLabel"
        :disable="sourceSelectionDisabled"
        :aria-label="`Dateiquelle: ${sourceLabel}`"
        toggle-aria-label="Dateiquelle wechseln"
      >
        <q-list padding>
          <q-item
            v-for="option in sourceOptions"
            :key="option.value"
            clickable
            v-ripple
            :active="workspace.scope === option.value"
            active-class="text-primary"
            :disable="option.disable"
            @click="selectSource(option.value)"
          >
            <q-item-section avatar><q-icon :name="option.icon" /></q-item-section>
            <q-item-section>{{ option.label }}</q-item-section>
            <q-item-section v-if="workspace.scope === option.value" side><q-icon name="check" color="primary" /></q-item-section>
          </q-item>
        </q-list>
      </q-btn-dropdown>
      <q-tooltip>{{ sourceTooltip }}</q-tooltip>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useConfirm } from 'src/composables/confirm'
import {
  formatByteSize,
  formatStorageUsage,
  storageUsageProgress
} from 'src/utils/resource-metrics'
import { useChatStore } from 'stores/chat'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileExplorerFooter' })

const { confirm } = useConfirm()
const chat = useChatStore()
const executionAgents = useExecutionAgentStore()
const workspace = useWorkspaceStore()
const selectedExecutionAgent = computed(() => (
  executionAgents.byId(chat.activeContext?.preferredExecutionAgentId)
))
const agentAvailable = computed(() => Boolean(
  selectedExecutionAgent.value?.connected
  && selectedExecutionAgent.value.operations?.includes('filesystem.list')
))
const sourceOptions = computed(() => [
  { label: 'Session', value: 'session', icon: 'inventory_2', disable: !workspace.sessionId },
  { label: 'Agent', value: 'filesystem', icon: 'computer', disable: !agentAvailable.value }
])
const sourceLabel = computed(() => workspace.scope === 'session' ? 'Session' : 'Agent')
const sourceIcon = computed(() => workspace.scope === 'session' ? 'inventory_2' : 'computer')
const sourceSelectionDisabled = computed(() => Boolean(
  workspace.toolContext
  || chat.running
  || chat.sessionSettingsPending > 0
  || !sourceOptions.value.some((option) => !option.disable && option.value !== workspace.scope)
))
const sourceTooltip = computed(() => {
  if (workspace.toolContext) return 'Gespeicherter Dateistand; wechsle zuerst zu den aktuellen Dateien.'
  if (!agentAvailable.value) return 'Kein Agent-Dateisystem verfügbar.'
  return 'Zwischen Session und Agent-Dateisystem wechseln.'
})
const storage = computed(() => (
  workspace.storageUsageSessionId === chat.current?.id ? workspace.storageUsage : null
))
const storageLabel = computed(() => formatStorageUsage(storage.value))
const storageProgress = computed(() => storageUsageProgress(storage.value))
const storageScopeLabel = computed(() => (
  storage.value?.scope === 'project' || chat.current?.projectId ? 'Projekt-Speicher' : 'Session-Speicher'
))
const storageDescription = computed(() => {
  if (workspace.storageUsageLoading && !storage.value) return `${storageScopeLabel.value}: Speichernutzung wird geladen.`
  if (!storage.value) return `${storageScopeLabel.value}: Speichernutzung nicht verfügbar.`
  const sharing = storage.value.scope === 'project' ? ', projektweit geteilt' : ''
  const stale = workspace.storageUsageFailed ? ' Letzter Stand.' : ''
  return `${storageScopeLabel.value}: ${formatByteSize(storage.value.usedBytes)} von ${formatByteSize(storage.value.quotaBytes)} belegt${sharing}.${stale}`
})

async function selectSource(scope) {
  if (scope === workspace.scope) return
  if (workspace.editorDirty) {
    const accepted = await confirm(
      'Sollen die ungespeicherten Änderungen verworfen werden?',
      'Änderungen verwerfen?',
      { okLabel: 'Verwerfen' }
    )
    if (!accepted) return
    workspace.discardEdit()
  }
  await workspace.setScope(scope)
}

watch(
  [() => chat.current?.id || null, () => chat.current?.projectId || null],
  ([sessionId]) => {
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
