import { defineStore } from 'pinia'
import { api } from 'src/boot/axios'
import { useChatStore } from 'src/features/chat/store'
import { useControlCenterStore } from 'stores/control-center'
import { useProjectStore } from 'stores/projects'
import { randomUuid } from 'src/utils/random-id'
import { Notify } from 'quasar'
import { useWorkspaceStore } from 'stores/workspace'
import { useAgentProfileStore } from 'stores/agent-profiles'

export function gitFinishEntry(entry) {
  return {
    id: randomUuid(), ownerUserId: entry.ownerUserId, sessionId: entry.sessionId, projectId: entry.projectId,
    content: '/git-finish', attachments: [], activeFile: null, options: entry.options, controlCenter: entry.controlCenter,
    agentProfileId: entry.agentProfileId || entry.options?.agentProfileId || null,
    afterRunId: entry.id, sourceType: 'followup', paused: false,
    queuedAt: new Date(Date.parse(entry.queuedAt) + 1).toISOString()
  }
}

export const useGitActionStore = defineStore('git-actions', {
  state: () => ({ managing: false, finishScope: null }),
  getters: {
    scope() {
      const chat = useChatStore(), context = chat.activeContext
      return JSON.stringify([chat.current?.id || chat.sessionsProjectId, context?.preferredExecutionAgentId, context?.currentDirectory])
    },
    finishNext() { return this.finishScope !== null && this.finishScope === this.scope },
    buildSelected() {
      const id = useChatStore().activeAgentProfileId
      return (useAgentProfileStore().byId(id)?.baseProfileId || id) === '00000000-0000-4000-8000-000000000004'
    },
    busy() {
      const chat = useChatStore()
      return Boolean(this.messageDisabled || chat.running || chat.runId || chat.current?.activeRun
        || chat.activeQueuedMessages.length || chat.queueDrainPromise)
    },
    messageDisabled() {
      const chat = useChatStore()
      return Boolean(this.managing || chat.loading || chat.outboxSaving || chat.historyActionPending
        || chat.sessionSettingsPending || !chat.outboxLoaded
        || chat.current?.readOnly || chat.current?.archived || chat.current?.archivedAt)
    }
  },
  actions: {
    setFinishNext(enabled) { this.finishScope = enabled ? this.scope : null },
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
    async run(name, argumentsList = [], commitFirst = false) {
      const chat = useChatStore()
      if (this.messageDisabled) return null
      if (!chat.current) {
        const context = chat.activeContext, projectId = chat.sessionsProjectId
        if (['git-init', 'git-remote'].includes(name) && context.workspaceMode === 'worktree') {
          await chat.updateActive({ workspace_mode: 'local' })
          if (chat.current || chat.activeContext !== context || chat.sessionsProjectId !== projectId) return null
        }
        const session = await chat.createEmptySession(useControlCenterStore())
        if (!session || chat.current?.id !== session.id) return null
        await chat.waitForDraftDefaults(projectId)
        if (chat.current?.id !== session.id) return null
      }
      if (this.messageDisabled) return null
      const sessionId = chat.current?.id
      const commitFlag = commitFirst && ['git-merge', 'git-pr'].includes(name) ? ' --commit' : ''
      const result = await chat.send(`/${name}${commitFlag}${argumentsList.map(value => ` ${JSON.stringify(value)}`).join('')}`, {
        preserveDraft: true, activeFile: null
      })
      return sessionId || result?.sessionId || null
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
