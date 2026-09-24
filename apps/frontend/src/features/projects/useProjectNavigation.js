import { computed, reactive, ref } from 'vue'
import { notifyError } from 'src/utils/notifications'

export function useProjectNavigation({
  projects, executionAgents, chat, controlCenter, workspace, automations,
  notifications, router, $q, confirm, selectedProjectId, confirmDiscardedEdit,
  openNewSession
}) {
  const projectsSectionOpen = ref(true)
  const expandedProjects = reactive({})
  const openProjectActionMenuId = ref(null)
  const projectDialogOpen = ref(false)
  const projectDialogMode = ref('create')
  const projectDialogTarget = ref(null)
  const projectName = ref('')
  const projectInstructions = ref('')
  const projectDefaultExecutionAgentId = ref(null)
  const projectDefaultCurrentDirectory = ref(null)
  const projectDirectoryPickerOpen = ref(false)
  const projectSaving = ref(false)
  const projectSelectedExecutionAgent = computed(() => (
    executionAgents.byId(projectDefaultExecutionAgentId.value)
  ))
  const projectExecutionAgentOptions = computed(() => {
    const options = [
      {
        label: 'Keine Projektvorgabe',
        value: null,
        connected: null,
        caption: 'Allgemeine Session-Vorgabe verwenden'
      },
      ...executionAgents.items.map((agent) => ({
        label: agent.alias,
        value: agent.id,
        connected: agent.connected,
        disable: !agent.connected && agent.id !== projectDefaultExecutionAgentId.value,
        caption: agent.connected ? agent.computerName || agent.platform : 'Offline'
      }))
    ]
    if (projectDefaultExecutionAgentId.value && !projectSelectedExecutionAgent.value) {
      options.push({
        label: 'Nicht verfügbarer Computer',
        value: projectDefaultExecutionAgentId.value,
        connected: false,
        caption: 'Nicht mehr eingerichtet'
      })
    }
    return options
  })
  const projectDirectoryPickerAvailable = computed(() => (
    projectSelectedExecutionAgent.value?.connected === true
    && projectSelectedExecutionAgent.value.operations?.includes('filesystem.list')
  ))

  async function loadProjects() {
    try {
      await projects.fetchAll()
    } catch {
      // The central API interceptor displays the error and the section exposes a retry action.
    }
  }

  function openProjectDialog(mode, project = null) {
    projectDialogMode.value = mode
    projectDialogTarget.value = project
    projectName.value = mode === 'edit' ? project?.name || '' : ''
    projectInstructions.value = mode === 'edit' ? project?.instructions || '' : ''
    projectDefaultExecutionAgentId.value = mode === 'edit'
      ? project?.defaultExecutionAgentId || null
      : null
    projectDefaultCurrentDirectory.value = mode === 'edit'
      ? project?.defaultCurrentDirectory || null
      : null
    projectDirectoryPickerOpen.value = false
    void executionAgents.fetchAll({ suppressErrorToast: true }).catch(() => {})
    projectDialogOpen.value = true
  }

  function selectProjectExecutionAgent(agentId) {
    projectDirectoryPickerOpen.value = false
    projectDefaultCurrentDirectory.value = executionAgents.byId(agentId)?.homeDirectory || null
  }

  function setProjectActionMenu(id, open) {
    if (open) openProjectActionMenuId.value = id
    else if (openProjectActionMenuId.value === id) openProjectActionMenuId.value = null
  }

  async function saveProject() {
    const name = projectName.value.trim()
    const instructions = projectInstructions.value.trim()
    const defaults = {
      defaultExecutionAgentId: projectDefaultExecutionAgentId.value,
      defaultCurrentDirectory: projectDefaultExecutionAgentId.value
        ? projectDefaultCurrentDirectory.value?.trim() || null
        : null
    }
    if (!name) return
    projectSaving.value = true
    try {
      if (projectDialogMode.value === 'create') {
        const project = await projects.create(name, instructions, defaults)
        projectDialogOpen.value = false
        $q.notify({ type: 'positive', message: 'Projekt erstellt.' })
        expandedProjects[project.id] = true
        await openNewSession(project.id)
      } else if (projectDialogTarget.value) {
        await projects.update(projectDialogTarget.value.id, name, instructions, defaults)
        projectDialogOpen.value = false
        $q.notify({ type: 'positive', message: 'Projekt gespeichert.' })
      }
    } catch {
      // The central API interceptor displays the error; the dialog remains open.
    } finally {
      projectSaving.value = false
    }
  }

  async function deleteProject(project) {
    if (!project) return
    let impact
    try {
      impact = await projects.deleteImpact(project.id, { suppressErrorToast: true })
    } catch (error) {
      notifyError(error)
      return
    }
    if (impact.fileReferenceCount) {
      $q.notify({
        type: 'negative',
        message: `${impact.fileReferenceCount} Dateireferenz${impact.fileReferenceCount === 1 ? '' : 'en'} ${impact.fileReferenceCount === 1 ? 'verhindert' : 'verhindern'} das Löschen dieses Projekts.`
      })
      return
    }
    const accepted = await confirm(
      `Projekt „${project.name}“ mit ${impact.sessionCount} ${impact.sessionCount === 1 ? 'Session' : 'Sessions'} sowie allen zugehörigen Dateien und Versionen dauerhaft löschen?`,
      'Projekt löschen?',
      { okLabel: 'Löschen', okColor: 'negative' }
    )
    if (!accepted) return
    let automationMode = null
    if (impact.requiredAutomationCount) {
      const count = impact.automationCount
      const automationsAccepted = await confirm(
        `Beim Fortfahren werden ${count} verknüpfte Automation${count === 1 ? '' : 'en'} samt Historie dauerhaft gelöscht.`,
        'Abhängige Automationen löschen?',
        { okLabel: 'Alles löschen' }
      )
      if (!automationsAccepted) return
      automationMode = 'deleteAutomations'
    } else if (impact.automationCount) {
      const count = impact.automationCount
      const automationsAccepted = await confirm(
        `Das Projekt enthält Ausgabeziele von ${count} Automation${count === 1 ? '' : 'en'}. Verknüpfungen lösen und Projekt löschen?`,
        'Verknüpfte Sessions löschen?',
        { okLabel: 'Lösen und löschen' }
      )
      if (!automationsAccepted) return
      automationMode = 'unlink'
    }
    const affectedTabs = controlCenter.fileTabs.filter((tab) => (
      tab.fileTarget.scope === 'session' && impact.sessionIds.includes(tab.fileTarget.rootIdentity)
    ))
    const dirtyAffectedTabs = affectedTabs.filter((tab) => workspace.fileTabDirty(tab.tabId))
    if (dirtyAffectedTabs.length && !await confirmDiscardedEdit({ discard: false })) return
    let outboxDeletion
    try {
      outboxDeletion = await chat.prepareProjectOutboxDeletion(project.id, impact.sessionIds)
    } catch {
      return
    }
    try {
      await projects.remove(project.id, {
        automationMode,
        automationImpact: impact.automationImpact
      })
    } catch {
      // The central API interceptor displays the error.
      await chat.restoreProjectOutboxDeletion(outboxDeletion)
      return
    }
    affectedTabs.forEach((tab) => workspace.discardFileTab(tab.tabId))
    delete expandedProjects[project.id]
    if (selectedProjectId.value === project.id) {
      try {
        await chat.setSessionScope(null)
      } catch {
        // Navigation still leaves the deleted scope; the standalone list keeps its retry state.
      }
    }
    await chat.removeProjectScope(project.id, impact.sessionIds, outboxDeletion)
    if (automationMode) {
      void automations.fetchAutomations({
        archived: automations.listArchived,
        suppressErrorToast: true
      }).catch(() => {})
      if (automationMode === 'deleteAutomations') {
        void notifications.fetchLatest({ suppressErrorToast: true }).catch(() => {})
      }
    }
    $q.notify({ type: 'positive', message: 'Projekt gelöscht.' })
    if (selectedProjectId.value === project.id) {
      await router.push({ name: 'chat' })
    }
  }

  return {
    projectsSectionOpen, expandedProjects, openProjectActionMenuId,
    projectDialogOpen, projectDialogMode, projectName, projectInstructions,
    projectDefaultExecutionAgentId, projectDefaultCurrentDirectory,
    projectDirectoryPickerOpen, projectSaving, projectSelectedExecutionAgent,
    projectExecutionAgentOptions, projectDirectoryPickerAvailable,
    loadProjects, openProjectDialog, selectProjectExecutionAgent,
    setProjectActionMenu, saveProject, deleteProject
  }
}
