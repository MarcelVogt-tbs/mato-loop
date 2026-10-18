import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useControlCenterStore } from 'stores/control-center'
import { parentPath } from 'src/features/files/state'

export function gitContext(session, target = null, known = null, diffActive = false) {
  if (!session?.id || !session.preferredExecutionAgentId || !session.currentDirectory) return null
  let currentDirectory = session.currentDirectory
  if (target?.scope === 'filesystem' && target.executionAgentId === session.preferredExecutionAgentId) {
    const root = known?.repository
    const sameRepository = root && known.sessionId === session.id && known.executionAgentId === target.executionAgentId
      && target.path.startsWith(`${root.replace(/\/$/u, '')}/`)
    currentDirectory = diffActive ? target.currentDirectory : sameRepository ? known.currentDirectory : parentPath(target.path)
  }
  return { sessionId: session.id, executionAgentId: session.preferredExecutionAgentId, currentDirectory }
}

export function gitContextKey(context) {
  return context ? JSON.stringify([context.sessionId, context.executionAgentId, context.currentDirectory]) : ''
}

function params(context, file = null) {
  return {
    session_id: context.sessionId,
    execution_agent_id: context.executionAgentId,
    current_directory: context.currentDirectory,
    ...(file != null ? { file } : {})
  }
}

export const useGitChangesStore = defineStore('git-changes', {
  state: () => ({ current: null, showChanges: false }),
  actions: {
    setContext(context) {
      if (gitContextKey(context) === gitContextKey(this.current)) return
      if (context?.sessionId !== this.current?.sessionId || context?.executionAgentId !== this.current?.executionAgentId) this.showChanges = false
      this.current = context ? {
        ...context, repository: null, files: [], truncated: false, loading: false, error: null,
        preview: null, previewLoading: false, previewError: null,
        listVersion: 0, previewVersion: 0
      } : null
    },
    async refresh() {
      const state = this.current
      if (!state) return
      const version = ++state.listVersion
      state.loading = true
      state.error = null
      try {
        const { data } = await api.get('/workspace/git-changes', { params: params(state) })
        if (version !== state.listVersion) return
        Object.assign(state, data)
        if (this.current === state && !state.repository) this.showChanges = false
      } catch (error) {
        if (version === state.listVersion) {
          state.error = error
          state.files = []
          state.preview = null
        }
      } finally {
        if (version === state.listVersion) state.loading = false
      }
    },
    async openFile(path) {
      const state = this.current
      if (!state?.repository || !state.files.some((file) => file.path === path)) return null
      return useControlCenterStore().openFile({
        scope: 'filesystem', path: `${state.repository.replace(/\/$/u, '')}/${path}`,
        name: path.split('/').at(-1), executionAgentId: state.executionAgentId,
        currentDirectory: state.repository
      }, { diff: true })
    },
    async loadDiff(path) {
      const state = this.current
      if (!state || !path) return
      const version = ++state.previewVersion
      state.preview = null
      state.previewError = null
      state.previewLoading = true
      try {
        const { data } = await api.get('/workspace/git-changes', { params: params(state, path) })
        if (version === state.previewVersion) state.preview = data
      } catch (error) {
        if (version === state.previewVersion) state.previewError = error
      } finally {
        if (version === state.previewVersion) state.previewLoading = false
      }
    }
  }
})
