import { reactive, ref } from 'vue'
import { notifyError } from 'src/utils/notifications'

export function useProjectNavigation({
  projects, chat, controlCenter, workspace, automations,
  notifications, router, $q, confirm, selectedProjectId, confirmDiscardedEdit,
  openNewSession
}) {
  const projectsSectionOpen = ref(true)
  const expandedProjects = reactive({})
  const openProjectActionMenuId = ref(null)
  const projectDialogOpen = ref(false)
  const projectDialogTarget = ref(null)

  async function loadProjects() {
    try {
      await projects.fetchAll()
    } catch {
      // The central API interceptor displays the error and the section exposes a retry action.
    }
  }

  function openProjectDialog(project = null) {
    projectDialogTarget.value = project
    projectDialogOpen.value = true
  }

  async function onProjectCreated(project) {
    expandedProjects[project.id] = true
    try {
      await openNewSession(project.id)
    } catch (error) {
      notifyError(error)
    }
  }

  function setProjectActionMenu(id, open) {
    if (open) openProjectActionMenuId.value = id
    else if (openProjectActionMenuId.value === id) openProjectActionMenuId.value = null
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
    projectDialogOpen, projectDialogTarget,
    loadProjects, openProjectDialog, onProjectCreated,
    setProjectActionMenu, deleteProject
  }
}
