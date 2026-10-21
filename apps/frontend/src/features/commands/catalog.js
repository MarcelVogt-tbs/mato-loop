import { computed, toValue, watch } from 'vue'
import { useChatStore } from 'src/features/chat/store'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useProjectStore } from 'stores/projects'
import { useCommandStore } from './store'

export function useCommandCatalog(enabled = true) {
  const chat = useChatStore(), agents = useExecutionAgentStore(), projects = useProjectStore()
  const commands = useCommandStore()
  const context = computed(() => ({
    session_id: chat.current?.id || null,
    project_id: (chat.current ? chat.current.projectId : chat.sessionsProjectId) || null,
    execution_agent_id: chat.activeContext?.preferredExecutionAgentId || null,
    current_directory: chat.activeContext?.currentDirectory || null,
    workspace_mode: chat.activeContext?.workspaceMode || 'local'
  }))
  const projectConfiguration = computed(() => Object.entries(projects.items.find(project => project.id === context.value.project_id) || {})
    .filter(([name]) => !['sessionCount', 'hasUnreadMessages', 'updatedAt'].includes(name)))
  const key = computed(() => JSON.stringify([context.value, chat.current?.codingWorkspace,
    chat.activeContext?.preferredExecutionAgentId ? agents.byId(chat.activeContext.preferredExecutionAgentId)?.connected : false,
    projectConfiguration.value]))
  const refresh = () => commands.load(context.value, key.value, true)
  watch([() => toValue(enabled), key], ([shown]) => {
    if (shown) void commands.load(context.value, key.value)
  }, { immediate: true })
  return { commands, refresh }
}
