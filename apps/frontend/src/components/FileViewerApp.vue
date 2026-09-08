<template>
  <FileViewerPanel
    :entry="workspace.viewedEntry"
    @close="removeTab"
  >
    <template v-if="$slots.toolbar" #toolbar><slot name="toolbar" /></template>
  </FileViewerPanel>
</template>

<script setup>
import { onBeforeUnmount } from 'vue'
import FileViewerPanel from 'components/FileViewerPanel.vue'
import { useConfirm } from 'src/composables/confirm'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useWorkspaceStore } from 'stores/workspace'

defineOptions({ name: 'FileViewerApp' })
const props = defineProps({ tab: { type: Object, required: true } })
const { confirm } = useConfirm()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const executionAgents = useExecutionAgentStore()
const workspace = useWorkspaceStore()
const target = props.tab.fileTarget
const agent = executionAgents.byId(target.executionAgentId)
let closing = false
const workspaceContext = target.scope === 'filesystem'
  ? {
      executionAgentId: target.executionAgentId,
      connected: Boolean(agent?.connected),
      operations: agent?.connected ? agent.operations || [] : []
    }
  : null

workspace.stashActiveFileTab()
workspace.configure({
  sessionId: controlCenter.sessionId || chat.current?.id || null,
  rootIdentity: target.scope === 'session' ? target.rootIdentity : chat.current?.projectId || chat.current?.id || null,
  currentDirectory: target.currentDirectory || chat.current?.currentDirectory || '/',
  workspaceContext,
  scope: target.scope
})
workspace.activateFileTab(props.tab, workspaceContext)

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
