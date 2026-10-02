import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useApprovalStore } from 'stores/approvals'
import { useAutomationStore } from 'stores/automations'
import { useBrowserStore } from 'stores/browser'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useIntegrationStore } from 'stores/integrations'
import { useProjectStore } from 'stores/projects'
import { useQuestionStore } from 'stores/questions'
import { useSearchStore } from 'stores/search'
import { useTerminalStore } from 'stores/terminal'
import { useWorkspaceStore } from 'stores/workspace'
import { terminalControlCenterState } from 'src/app/control-center/manifest'
import { isPwaUpdateReload } from 'src/utils/pwa-update'
import { searchShortcutAction } from 'src/utils/search-shortcut'

const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'waiting_approval', 'waiting_input'])
const SESSION_ACTIVITY_REFRESH_MS = 5000

export function useShellSync({ drawerOpen, controlCenterVisible }) {
  const $q = useQuasar()
  const route = useRoute()
  const identity = useAgentIdentityStore()
  const approvals = useApprovalStore()
  const automations = useAutomationStore()
  const browser = useBrowserStore()
  const chat = useChatStore()
  const controlCenter = useControlCenterStore()
  const executionAgents = useExecutionAgentStore()
  const notifications = useInAppNotificationStore()
  const integrations = useIntegrationStore()
  const projects = useProjectStore()
  const questions = useQuestionStore()
  const search = useSearchStore()
  const terminal = useTerminalStore()
  const workspace = useWorkspaceStore()
  const routeSessionId = computed(() => (
    typeof route.query.session === 'string' ? route.query.session : null
  ))
  const terminalContextState = computed(() => terminalControlCenterState(chat.current, executionAgents))
  const visualViewportHeight = ref(null)
  const visualViewportOffsetTop = ref(0)
  const visualViewportStyle = computed(() => {
    if (route.name !== 'chat' || visualViewportHeight.value === null) return null
    return {
      '--app-visual-viewport-height': `${visualViewportHeight.value}px`,
      '--app-visual-viewport-offset-top': `${visualViewportOffsetTop.value}px`
    }
  })
  let sessionActivityTimer = null
  let sessionActivityRefreshPending = false

  function updateVisualViewport() {
    const viewport = window.visualViewport
    const height = viewport?.height || window.innerHeight
    const offsetTop = viewport?.offsetTop || 0
    if (Number.isFinite(height) && height > 0) visualViewportHeight.value = height
    visualViewportOffsetTop.value = Number.isFinite(offsetTop) ? Math.max(0, offsetTop) : 0
  }

  function handleSearchShortcut(event) {
    const action = searchShortcutAction(event, search.dialogOpen)
    if (action === 'app') {
      event.preventDefault()
      search.openDialog()
    } else if (action === 'browser') {
      search.dialogOpen = false
    }
  }

  function appIsFocused() {
    return document.visibilityState === 'visible' && document.hasFocus()
  }

  function refreshAppPresence() {
    if (!appIsFocused()) return
    void notifications.markPresent().catch(() => {})
  }

  function refreshSessionsWhenVisible() {
    if (document.visibilityState !== 'visible') return
    refreshAppPresence()
    if (projects.loadFailed) void projects.fetchAll().catch(() => {})
    if (chat.current?.id && !chat.running) {
      void chat.refreshSession(chat.current.id, { suppressErrorToast: true }).catch(() => {})
    }
    for (const [scope, list] of Object.entries(chat.sessionLists)) {
      if (list.loaded) void chat.refreshSessions(null, scope === 'standalone' ? null : scope).catch(() => {})
    }
    void approvals.fetchPending({ suppressErrorToast: true }).catch(() => {})
    void questions.fetchPending({ suppressErrorToast: true }).catch(() => {})
    void notifications.fetchLatest({ suppressErrorToast: true }).catch(() => {})
  }

  function refreshPushState() {
    void notifications.reconcilePush({ suppressErrorToast: true }).catch(() => {})
  }

  function handlePushMessage(event) {
    if (event.data?.type !== 'matoloop:push' || event.data.version !== 1) return
    void Promise.allSettled([
      notifications.fetchLatest({ suppressErrorToast: true }),
      approvals.fetchPending({ suppressErrorToast: true }),
      questions.fetchPending({ suppressErrorToast: true })
    ])
  }

  async function refreshActiveSessions() {
    if (document.visibilityState !== 'visible') return
    refreshAppPresence()
    if (sessionActivityRefreshPending) return
    sessionActivityRefreshPending = true
    try {
      const requests = [
        automations.fetchAutomations({ includeArchived: true, suppressErrorToast: true }),
        approvals.fetchPending({ suppressErrorToast: true }),
        questions.fetchPending({ suppressErrorToast: true })
      ]
      if (appIsFocused()) requests.push(notifications.fetchLatest({ suppressErrorToast: true }))
      if (chat.current?.id && !chat.running) {
        requests.push(chat.refreshSession(chat.current.id, { suppressErrorToast: true }))
      }
      if (chat.current?.id && chat.running) void browser.reconcileControlRequest(chat.current.id)
      for (const [scope, list] of Object.entries(chat.sessionLists)) {
        if (list.items.some((session) => ACTIVE_RUN_STATUSES.has(session.activeRunStatus))) {
          requests.push(chat.refreshSessions(
            { suppressErrorToast: true },
            scope === 'standalone' ? null : scope
          ))
        }
      }
      await Promise.all(requests)
    } catch {
      // The next interval reconciles transient background refresh failures.
    } finally {
      sessionActivityRefreshPending = false
    }
  }

  function preventDiscardOnUnload(event) {
    if (isPwaUpdateReload() || !workspace.hasDirtyEditors) return
    event.preventDefault()
    event.returnValue = ''
  }

  watch(
    () => [controlCenterVisible.value, $q.screen.lt.lg],
    ([open, narrow]) => {
      if (open && narrow) drawerOpen.value = false
    }
  )

  watch(
    () => [
      chat.current?.id || null,
      chat.current?.preferredExecutionAgentId || null,
      terminalContextState.value.agent?.connected === true,
      terminalContextState.value.available,
      controlCenter.tabs.some((tab) => tab.appId === 'terminal'),
      route.name === 'chat'
    ],
    ([sessionId, agentId, connected, supported, tabPresent, onChatRoute]) => {
      terminal.syncContext({ sessionId, agentId, connected, supported, tabPresent, onChatRoute })
    },
    { immediate: true }
  )

  watch(() => route.name, (name) => {
    if (name === 'chat') void Promise.allSettled([
      integrations.fetchCatalog(), integrations.fetchBrowser(), integrations.fetchTbsDesk()
    ])
  }, { immediate: true })

  watch([() => chat.current?.id, () => controlCenter.ready,
    () => automations.items.some((item) => item.executionSessionId === chat.current?.id)],
  ([id, ready, configured]) => {
    if (id && ready && configured) void controlCenter.ensureApp('automation')?.catch(() => {})
  }, { immediate: true })

  watch(() => route.name === 'chat' && routeSessionId.value === chat.current?.id ? chat.current?.id : null,
    (id) => browser.setDisplayedSession(id), { immediate: true, flush: 'sync' })

  watch(() => [
    chat.current?.id, chat.current?.preferredExecutionAgentId, browser.sessionId,
    executionAgents.byId(browser.agentId)?.connected === true
      && (!integrations.catalogLoaded || integrations.catalogLoadFailed || integrations.adapterAvailable('browser', 'personal'))
      && (!integrations.browserLoaded || integrations.browser.instances.some((item) => item.id === browser.integrationId
        && item.executionAgentId === browser.agentId && item.enabled)),
    controlCenter.sessionId === browser.sessionId && controlCenter.ready
      ? controlCenter.confirmedState.tabs.some((tab) => tab.appId === 'browser') : true
  ], ([sessionId, agentId, , available, tabPresent]) => {
    browser.syncContext({ sessionId, agentId, available, tabPresent })
  }, { immediate: true, flush: 'post' })

  watch(() => [chat.current?.id, routeSessionId.value, route.name, controlCenter.ready], ([sessionId, routeId, name]) => {
    if (name === 'chat' && sessionId === routeId) void browser.reconcileControlRequest(sessionId)
  }, { immediate: true, flush: 'sync' })

  onMounted(async () => {
    updateVisualViewport()
    window.visualViewport?.addEventListener('resize', updateVisualViewport)
    window.visualViewport?.addEventListener('scroll', updateVisualViewport)
    window.addEventListener('resize', updateVisualViewport)
    window.addEventListener('keydown', handleSearchShortcut)
    window.addEventListener('focus', refreshAppPresence)
    window.addEventListener('beforeunload', preventDiscardOnUnload)
    document.addEventListener('visibilitychange', refreshSessionsWhenVisible)
    window.addEventListener('matoloop:service-worker-ready', refreshPushState)
    window.navigator.serviceWorker?.addEventListener('message', handlePushMessage)
    sessionActivityTimer = window.setInterval(refreshActiveSessions, SESSION_ACTIVITY_REFRESH_MS)
    refreshAppPresence()
    await Promise.allSettled([
      approvals.fetchPending(),
      automations.fetchAutomations({ includeArchived: true, suppressErrorToast: true }),
      questions.fetchPending(),
      notifications.fetchLatest({ suppressErrorToast: true }),
      notifications.reconcilePush({ suppressErrorToast: true }),
      identity.fetchIdentity()
    ])
  })

  onBeforeUnmount(() => {
    window.visualViewport?.removeEventListener('resize', updateVisualViewport)
    window.visualViewport?.removeEventListener('scroll', updateVisualViewport)
    window.removeEventListener('resize', updateVisualViewport)
    window.removeEventListener('keydown', handleSearchShortcut)
    window.removeEventListener('focus', refreshAppPresence)
    window.removeEventListener('beforeunload', preventDiscardOnUnload)
    terminal.release()
    browser.release()
    document.removeEventListener('visibilitychange', refreshSessionsWhenVisible)
    window.removeEventListener('matoloop:service-worker-ready', refreshPushState)
    window.navigator.serviceWorker?.removeEventListener('message', handlePushMessage)
    window.clearInterval(sessionActivityTimer)
    sessionActivityTimer = null
  })

  return { visualViewportStyle }
}
