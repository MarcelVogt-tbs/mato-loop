<template>
  <FileViewerPanel
    :entry="workspace.viewedEntry"
    @close="removeTab"
  >
    <template v-if="$slots.toolbar" #toolbar><slot name="toolbar" /></template>
  </FileViewerPanel>
</template>

<script setup>
import { computed, onBeforeUnmount, watch } from 'vue'
import FileViewerPanel from 'src/features/files/components/FileViewerPanel.vue'
import { useConfirm } from 'src/composables/confirm'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useWorkspaceStore, workspaceContextKey } from 'stores/workspace'

defineOptions({ name: 'FileViewerApp' })
const props = defineProps({ tab: { type: Object, required: true } })
const { confirm } = useConfirm()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const executionAgents = useExecutionAgentStore()
const workspace = useWorkspaceStore()
const target = props.tab.fileTarget
let closing = false
const workspaceContext = computed(() => target.scope === 'filesystem'
  ? executionAgents.workspaceContextFor({ preferredExecutionAgentId: target.executionAgentId })
  : null)

workspace.stashActiveFileTab()
workspace.configure({
  sessionId: controlCenter.sessionId || chat.current?.id || null,
  rootIdentity: target.scope === 'session' ? target.rootIdentity : chat.current?.id || null,
  currentDirectory: target.currentDirectory || chat.current?.currentDirectory || '/',
  workspaceContext: workspaceContext.value,
  scope: target.scope
})
workspace.activateFileTab(props.tab, workspaceContext.value)

watch(() => workspaceContextKey(workspaceContext.value), () => {
  workspace.workspaceContext = workspaceContext.value
})

async function confirmDiscard() {
  if (!workspace.fileTabDirty(props.tab.tabId)) return true
  return confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
}

async function removeTab() {
  if (!await confirmDiscard()) return
  closing = true
  try {
    await controlCenter.closeFile(props.tab.tabId)
    workspace.discardFileTab(props.tab.tabId)
  } catch {
    closing = false
    // The central API interceptor displays the error and keeps the draft available.
  }
}

onBeforeUnmount(() => {
  if (closing || controlCenter.saving || controlCenter.tabs.some((tab) => tab.tabId === props.tab.tabId)) {
    workspace.stashActiveFileTab(props.tab.tabId)
  } else {
    workspace.discardFileTab(props.tab.tabId)
  }
})
</script>
