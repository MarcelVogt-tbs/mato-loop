<template>
  <AppSelect
    :model-value="available ? session.workspaceMode === 'worktree' ? session.workspacePath || 'new' : 'local' : null"
    :options="options"
    :display-value="!available ? 'Nicht verfügbar' : undefined"
    :readonly="Boolean(session.id) || !available"
    :hide-dropdown-icon="Boolean(session.id) || !available"
    :disable="disabled"
    label="Arbeitsbereich" aria-label="Arbeitsbereich"
    borderless dense stack-label emit-value map-options hide-bottom-space
    class="chat-start-select q-px-sm text-body2"
    @popup-show="refresh"
    @update:model-value="select"
  >
    <template #prepend><q-icon name="workspaces" size="18px" /></template>
  </AppSelect>
</template>

<script setup>
import { computed } from 'vue'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useProjectStore } from 'stores/projects'
import { useRepositoryProbe } from './repository'
import { apiErrorMessage } from 'src/utils/api-errors'

const props = defineProps({
  session: { type: Object, required: true },
  projectId: { type: String, default: null },
  disabled: Boolean
})
const emit = defineEmits(['update'])
const agents = useExecutionAgentStore(), projects = useProjectStore()
const available = computed(() => Boolean(props.session.preferredExecutionAgentId && props.session.currentDirectory))
const project = computed(() => !props.session.id && props.projectId ? projects.items.find(item => item.id === props.projectId
  && item.defaultExecutionAgentId === props.session.preferredExecutionAgentId
  && item.defaultCurrentDirectory === props.session.currentDirectory) : null)
const { repository, loading, error, refresh } = useRepositoryProbe(
  () => props.session.preferredExecutionAgentId, () => props.session.currentDirectory,
  () => !props.session.id && available.value && Boolean(project.value),
  () => project.value?.gitTargetBranch
)
function select(value) {
  emit('update', { workspace_mode: value === 'local' ? 'local' : 'worktree', workspace_path: ['local', 'new'].includes(value) ? null : value })
}
const options = computed(() => {
  const target = project.value?.gitTargetBranch || repository.value?.defaultBranch
  const reason = !props.session.sessionProfile?.worktreesEnabled ? apiErrorMessage({ error: { code: 'sessionTemplate.worktreesDisabled' } })
    : !project.value ? 'Projekt mit passendem Computer und Repository erforderlich.'
    : !agents.byId(props.session.preferredExecutionAgentId)?.connected ? 'Der Computer ist offline.'
      : error.value ? apiErrorMessage(error.value)
      : loading.value || !repository.value ? 'Repository wird geprüft.'
        : !agents.byId(props.session.preferredExecutionAgentId)?.operations?.includes('git.worktree') ? 'Der Computer bietet keine Worktrees an.'
          : !repository.value.repository ? 'Ein Git-Repository ist erforderlich.'
            : null
  const creationReason = reason || (!target ? 'Ein Zielbranch ist erforderlich.' : repository.value?.targetHasCommit === false ? 'Der lokale Zielbranch benötigt einen Commit.' : null)
  return [
    { label: 'Lokal', value: 'local' },
    { label: 'Neuer Worktree', value: 'new', disable: Boolean(creationReason), caption: creationReason },
    ...(project.value ? (repository.value?.worktrees || []).filter(item => item.path !== project.value.defaultCurrentDirectory).map((item, index) => ({
      label: `${item.path.split('/').pop()} · ${item.branch || 'Detached HEAD'}`, value: item.path,
      separatorBefore: index === 0, disable: Boolean(reason) || item.locked, caption: item.locked ? 'Worktree ist in Git gesperrt.' : null
    })) : [])
  ]
})
</script>
