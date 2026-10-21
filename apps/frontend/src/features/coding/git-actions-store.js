import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useChatStore } from 'src/features/chat/store'
import { useControlCenterStore } from 'stores/control-center'
import { useProjectStore } from 'stores/projects'
import { randomUuid } from 'src/utils/random-id'

export const useGitActionStore = defineStore('git-actions', {
  getters: {
    busy() {
      const chat = useChatStore()
      return Boolean(chat.running || chat.runId || chat.current?.activeRun || chat.outboxSaving || chat.historyActionPending
        || chat.sessionSettingsPending || chat.activeQueuedMessages.length || chat.queueDrainPromise
        || !chat.outboxLoaded || chat.current?.readOnly || chat.current?.archived || chat.current?.archivedAt)
    }
  },
  actions: {
    async run(name, argumentsList = []) {
      const chat = useChatStore()
      if (this.busy) return null
      if (!chat.current) {
        const context = chat.activeContext, projectId = chat.sessionsProjectId
        if (['git_init', 'git_remote'].includes(name) && context.workspaceMode === 'worktree') {
          await chat.updateActive({ workspace_mode: 'local' })
          if (chat.current || chat.activeContext !== context || chat.sessionsProjectId !== projectId) return null
        }
        const session = await chat.createEmptySession(useControlCenterStore())
        if (!session || chat.current?.id !== session.id) return null
      }
      if (this.busy) return null
      const sessionId = chat.current.id
      await chat.sendToCurrent(`/${name}${argumentsList.map(value => ` ${JSON.stringify(value)}`).join('')}`, [], {
        clearDraft: false, activeFile: null, runId: randomUuid(), agentProfileId: chat.activeAgentProfileId
      })
      return sessionId
    },
    async useRemote(remote, remoteIdentity) {
      const chat = useChatStore()
      if (!chat.current) return null
      const id = chat.current.id
      const { data } = await api.put(`/coding/sessions/${id}/target`, { remote, remote_identity: remoteIdentity })
      if (chat.current?.id === id) chat.current.codingWorkspace = data.codingWorkspace
      chat.updateCachedSession(id, session => ({ ...session, codingWorkspace: data.codingWorkspace }))
      if (data.projectId) {
        const projects = useProjectStore()
        projects.revision += 1
        projects.items = projects.items.map(project => project.id === data.projectId ? { ...project, gitRemote: remote } : project)
      }
      return id
    }
  }
})
