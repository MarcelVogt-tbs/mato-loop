import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useChatStore } from 'src/features/chat/store'
import { useControlCenterStore } from 'stores/control-center'
import { useProjectStore } from 'stores/projects'
import { randomUuid } from 'src/utils/random-id'
import { Notify } from 'quasar'
import { useWorkspaceStore } from 'stores/workspace'

export const useGitActionStore = defineStore('git-actions', {
  state: () => ({ managing: false }),
  getters: {
    busy() {
      const chat = useChatStore()
      return Boolean(this.managing || chat.running || chat.runId || chat.current?.activeRun || chat.outboxSaving || chat.historyActionPending
        || chat.sessionSettingsPending || chat.activeQueuedMessages.length || chat.queueDrainPromise
        || !chat.outboxLoaded || chat.current?.readOnly || chat.current?.archived || chat.current?.archivedAt)
    }
  },
  actions: {
    async workspaceAction(action, values = {}) {
      const chat = useChatStore(), session = chat.current
      if (!session || this.busy) return false
      if (useWorkspaceStore().hasDirtyEditors) {
        Notify.create({ type: 'warning', message: 'Bitte zuerst die ungespeicherten Editoränderungen speichern oder verwerfen.' })
        return false
      }
      this.managing = true
      try {
        if (action === 'select') {
          const { data } = await api.put(`/coding/sessions/${session.id}/workspace`, {
            ...values, ...(values.create ? { worktree_id: randomUuid() } : {})
          })
          chat.updateCachedSession(session.id, item => ({ ...item, ...data }))
          if (chat.current?.id === session.id) {
            Object.assign(chat.current, data)
            useControlCenterStore().syncSession(chat.current)
          }
        } else {
          await api.post(`/coding/sessions/${session.id}/git`, { action, directory: session.currentDirectory, ...values })
          await chat.refreshSession(session.id)
        }
        return true
      } finally { this.managing = false }
    },
    async setTarget(targetBranch) {
      const chat = useChatStore()
      if (!chat.current || this.busy) return
      chat.sessionSettingsPending += 1
      try {
        await this.updateTarget({ target_branch: targetBranch })
      } finally { chat.sessionSettingsPending -= 1 }
    },
    async run(name, argumentsList = []) {
      const chat = useChatStore()
      if (this.busy) return null
      if (!chat.current) {
        const context = chat.activeContext, projectId = chat.sessionsProjectId
        if (['git-init', 'git-remote'].includes(name) && context.workspaceMode === 'worktree') {
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
    async updateTarget(values) {
      const chat = useChatStore()
      if (!chat.current) return null
      const id = chat.current.id
      const { data } = await api.put(`/coding/sessions/${id}/target`, values)
      if (chat.current?.id === id) chat.current.codingWorkspace = data.codingWorkspace
      chat.updateCachedSession(id, session => ({ ...session, codingWorkspace: data.codingWorkspace }))
      if (data.projectId && values.remote !== undefined) {
        const projects = useProjectStore()
        projects.revision += 1
        projects.items = projects.items.map(project => project.id === data.projectId ? { ...project, gitRemote: values.remote } : project)
      }
      return id
    }
  }
})
