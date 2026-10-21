import { ref, toValue, watch, onScopeDispose } from 'vue'
import { api } from 'src/boot/axios'
import { useExecutionAgentStore } from 'stores/execution-agents'

export function useRepositoryProbe(agentId, directory, enabled = true, targetBranch = null) {
  const agents = useExecutionAgentStore()
  const repository = ref(null)
  const loading = ref(false)
  const error = ref(null)
  let version = 0
  let timer

  async function refresh() {
    clearTimeout(timer)
    const request = ++version
    error.value = null
    loading.value = false
    const id = toValue(agentId), path = toValue(directory)
    if (!toValue(enabled) || !path) return
    const agent = agents.byId(id)
    if (!agent?.connected || !agent.operations?.includes('git.repository')) return
    loading.value = true
    try {
      const { data } = await api.post('/coding/repository', {
        execution_agent_id: id, current_directory: path, target_branch: toValue(targetBranch) || null
      }, { suppressErrorToast: true })
      if (request === version) repository.value = data
    } catch (cause) {
      if (request === version) error.value = cause
    } finally {
      if (request === version) loading.value = false
    }
  }

  watch([
    () => toValue(enabled),
    () => toValue(agentId),
    () => toValue(directory),
    () => toValue(enabled) && agents.byId(toValue(agentId))?.connected === true,
    () => toValue(enabled) && agents.byId(toValue(agentId))?.operations?.includes('git.repository') === true,
    () => toValue(targetBranch)
  ], (values, previous = []) => {
    ++version
    if (values[1] !== previous[1] || values[2] !== previous[2]) repository.value = null
    error.value = null
    loading.value = false
    clearTimeout(timer)
    timer = setTimeout(refresh, 300)
  }, { immediate: true })
  onScopeDispose(() => { ++version; clearTimeout(timer) })
  return { repository, loading, error, refresh }
}
