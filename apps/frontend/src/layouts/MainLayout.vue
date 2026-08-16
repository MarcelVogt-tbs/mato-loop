<template>
  <q-layout
    view="hHh Lpr fFf"
    class="app-shell"
    :class="{
      'app-shell--chat': route.name === 'chat',
      'app-shell--navigation-overlay': showFloatingNavigation,
      'app-shell--floating-chat-controls': route.name === 'chat' && showFloatingControls
    }"
    :style="visualViewportStyle"
  >
    <q-drawer
      id="app-navigation-drawer"
      v-model="drawerOpen"
      show-if-above
      bordered
      :width="288"
      :breakpoint="1023"
      class="app-drawer"
    >
      <div class="column full-height no-wrap drawer-layout">
        <q-toolbar class="drawer-toolbar q-px-sm">
          <router-link
            to="/"
            class="app-brand row no-wrap items-center q-gutter-sm"
            @click="closeDrawerOnMobile"
          >
            <AppLogo />
            <div>
              <div class="app-brand__name">MatoLoop</div>
              <div class="app-brand__tagline">Personal Agent Workspace</div>
            </div>
          </router-link>

          <q-space />

          <q-btn
            flat
            round
            dense
            :icon="notifications.unreadCount || attentionCount ? 'notifications' : 'notifications_none'"
            :aria-label="notificationBellLabel"
            @click="openNotifications"
          >
            <q-badge
              v-if="notificationBadgeCount"
              floating
              rounded
              :color="attentionCount ? 'warning' : 'primary'"
              :text-color="attentionCount ? 'dark' : 'white'"
              :label="notificationBadgeLabel"
              aria-hidden="true"
            />
            <q-tooltip>Benachrichtigungen</q-tooltip>
          </q-btn>

          <q-btn
            flat
            round
            dense
            icon="menu"
            aria-label="Navigation schließen"
            @click="closeNavigation"
          />
        </q-toolbar>
        <q-separator class="lt-md" />

        <div class="col column no-wrap drawer-navigation q-py-sm">
          <section class="col drawer-section drawer-section--sessions" aria-labelledby="sessions-heading">
            <q-item dense tag="div" class="q-px-md q-py-xs">
              <q-item-section>
                <q-item-label
                  id="sessions-heading"
                  header
                  class="q-pa-none text-subtitle2 text-weight-medium"
                  :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                >
                  Sessions
                </q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn
                  flat
                  round
                  dense
                  size="sm"
                  icon="add_comment"
                  class="drawer-section__action"
                  :color="$q.dark.isActive ? 'grey-6' : 'grey-7'"
                  aria-label="Neue Session"
                  @click="openNewSession"
                >
                  <q-tooltip>Neue Session</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>

            <div class="drawer-section__body column no-wrap">
              <q-scroll-area
                class="col session-scroll-area"
                visible
                tabindex="0"
                aria-label="Session-Liste"
              >
                <q-infinite-scroll
                  ref="sessionInfiniteScroll"
                  :offset="40"
                  :disable="!chat.sessionsNextCursor || chat.sessionsLoadMoreFailed"
                  @load="loadMoreSessions"
                >
                <q-list id="session-list" class="session-list q-px-sm">
                <q-item v-if="chat.sessionsLoaded && !chat.sessions.length" dense class="q-px-sm q-py-xs">
                  <q-item-section>
                    <q-item-label class="text-body2">Noch keine Sessions</q-item-label>
                    <q-item-label caption class="text-caption">Starte eine neue Unterhaltung.</q-item-label>
                  </q-item-section>
                </q-item>
                <template v-for="group in groupedSessions" :key="group.key">
                  <q-item-label
                    header
                    class="q-px-sm q-pt-md q-pb-xs text-caption text-weight-medium"
                    :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                  >
                    {{ group.label }}
                  </q-item-label>
                  <q-item
                    v-for="session in group.sessions"
                    :key="session.id"
                    clickable
                    dense
                    v-ripple
                    class="session-list-item items-center rounded-borders q-px-sm q-py-xs q-my-xs"
                    :class="{ 'session-list-item--menu-open': openActionMenuSessionId === session.id }"
                    :active="route.query.session === session.id"
                    :active-class="$q.dark.isActive ? 'bg-grey-9 text-white' : 'bg-grey-3 text-dark'"
                    @click="openSession(session.id)"
                  >
                    <q-item-section class="session-list-item__content">
                      <q-item-label
                        lines="1"
                        class="session-list-item__title text-body2"
                        :class="{
                          'text-weight-bold': session.hasUnreadMessages,
                          'text-weight-medium': !session.hasUnreadMessages && route.query.session === session.id
                        }"
                      >
                        {{ session.title }}
                        <span v-if="session.hasUnreadMessages" class="app-sr-only">, ungelesene Nachrichten</span>
                      </q-item-label>
                      <q-tooltip
                        v-if="$q.platform.is.mobile !== true"
                        :delay="700"
                        anchor="bottom middle"
                        self="top middle"
                        :offset="[0, 8]"
                        class="session-title-tooltip"
                      >
                        <div>{{ session.title }}</div>
                        <div v-if="session.currentDirectory || session.workingDirectory" class="q-mt-xs text-grey-4">
                          {{ session.currentDirectory || session.workingDirectory }}
                        </div>
                      </q-tooltip>
                    </q-item-section>
                    <q-item-section side class="session-list-item__actions row no-wrap items-center self-center">
                      <span
                        v-if="sessionStatuses.get(session.id)"
                        class="session-list-item__status-slot row items-center justify-center q-mr-xs"
                        role="status"
                        :aria-label="sessionStatuses.get(session.id)?.label"
                      >
                        <template v-if="sessionStatuses.get(session.id)?.kind === 'approval'">
                          <q-icon name="pending_actions" color="warning" size="18px" />
                          <q-badge
                            v-if="sessionStatuses.get(session.id).count > 1"
                            floating
                            rounded
                            color="warning"
                            text-color="dark"
                            :label="sessionStatuses.get(session.id).count"
                          />
                        </template>
                        <q-spinner-dots
                          v-else-if="sessionStatuses.get(session.id)?.kind === 'active'"
                          color="primary"
                          size="18px"
                        />
                        <q-tooltip v-if="sessionStatuses.get(session.id) && $q.platform.is.mobile !== true">
                          {{ sessionStatuses.get(session.id).label }}
                        </q-tooltip>
                      </span>
                      <q-btn
                        flat
                        round
                        dense
                        size="sm"
                        icon="push_pin"
                        class="session-list-item__action session-list-item__pin-button"
                        :class="{ 'session-list-item__pin-button--pinned': session.pinned }"
                        :color="session.pinned ? 'primary' : ($q.dark.isActive ? 'grey-5' : 'grey-7')"
                        :loading="pinningSessionId === session.id"
                        :aria-label="session.pinned ? `${session.title} nicht mehr anheften` : `${session.title} anheften`"
                        @click.stop="toggleSessionPinned(session)"
                      >
                        <q-tooltip>{{ session.pinned ? 'Nicht mehr anheften' : 'Anheften' }}</q-tooltip>
                      </q-btn>
                      <q-btn
                        flat
                        round
                        dense
                        size="sm"
                        icon="more_horiz"
                        class="session-list-item__action session-list-item__menu-button"
                        :color="$q.dark.isActive ? 'grey-5' : 'grey-7'"
                        :aria-label="`Aktionen für ${session.title}`"
                        @click.stop
                      >
                        <q-menu
                          anchor="bottom right"
                          self="top right"
                          :offset="[0, 8]"
                          class="app-action-menu"
                          @before-show="openActionMenuSessionId = session.id"
                          @hide="closeActionMenu(session.id)"
                        >
                          <q-list padding class="session-actions-menu">
                            <q-item clickable v-ripple v-close-popup @click="openRenameDialog(session)">
                              <q-item-section avatar><q-icon name="edit" /></q-item-section>
                              <q-item-section>Umbenennen</q-item-section>
                            </q-item>
                            <q-separator class="q-my-xs" />
                            <q-item
                              clickable
                              v-ripple
                              v-close-popup
                              class="text-negative"
                              v-confirm="{ message: `Session „${session.title}“ löschen?`, title: 'Session löschen', okLabel: 'Löschen' }"
                              @click="removeSession(session.id)"
                            >
                              <q-item-section avatar><q-icon name="delete_outline" /></q-item-section>
                              <q-item-section>Löschen</q-item-section>
                            </q-item>
                          </q-list>
                        </q-menu>
                      </q-btn>
                    </q-item-section>
                  </q-item>
                </template>
                </q-list>
                  <template #loading>
                    <div class="row justify-center" aria-label="Weitere Sessions werden geladen">
                      <q-spinner-dots color="primary" size="24px" />
                    </div>
                  </template>
                </q-infinite-scroll>
                <div v-if="chat.sessionsLoadMoreFailed" class="q-px-md q-pb-sm">
                  <q-btn
                    flat
                    dense
                    no-caps
                    class="full-width"
                    color="primary"
                    label="Weitere Sessions laden"
                    icon-right="refresh"
                    @click="retrySessionPagination"
                  />
                </div>
              </q-scroll-area>
            </div>
          </section>

        </div>

        <div class="drawer-user-area q-pa-sm">
          <q-btn
            unelevated
            no-caps
            class="full-width app-user-menu-trigger"
            :aria-label="userMenuLabel"
          >
            <div class="row no-wrap items-center full-width">
              <q-avatar color="primary" text-color="white" size="36px">
                {{ userInitials }}
              </q-avatar>
              <div class="col ellipsis text-left text-body2 q-ml-md">
                {{ auth.username || 'Benutzer' }}
              </div>
              <q-icon name="keyboard_arrow_up" size="20px" class="q-ml-sm" />
            </div>

            <q-menu
              anchor="top right"
              self="bottom right"
              :offset="[0, 8]"
              class="app-user-popup app-action-menu"
            >
              <q-list padding>
                <q-item
                  clickable
                  v-ripple
                  :to="{ name: 'settings-approvals' }"
                  v-close-popup
                  @click="closeDrawerOnMobile"
                >
                  <q-item-section avatar><q-icon name="pending_actions" /></q-item-section>
                  <q-item-section>Freigaben</q-item-section>
                  <q-item-section v-if="approvals.pendingCount" side>
                    <q-badge color="warning" text-color="dark" :label="approvals.pendingCount" />
                  </q-item-section>
                </q-item>
                <q-item clickable v-ripple to="/settings" v-close-popup @click="closeDrawerOnMobile">
                  <q-item-section avatar><q-icon name="tune" /></q-item-section>
                  <q-item-section>Einstellungen</q-item-section>
                </q-item>
                <q-item clickable v-ripple v-close-popup @click="$q.dark.toggle()">
                  <q-item-section avatar>
                    <q-icon :name="$q.dark.isActive ? 'light_mode' : 'dark_mode'" />
                  </q-item-section>
                  <q-item-section>
                    {{ $q.dark.isActive ? 'Heller Modus' : 'Dunkler Modus' }}
                  </q-item-section>
                </q-item>
                <q-item
                  clickable
                  v-ripple
                  href="/docs/"
                  target="_blank"
                  rel="noopener noreferrer"
                  v-close-popup
                >
                  <q-item-section avatar><q-icon name="menu_book" /></q-item-section>
                  <q-item-section>Dokumentation</q-item-section>
                </q-item>
                <q-separator class="q-my-sm" />
                <q-item clickable v-ripple v-close-popup class="text-negative" @click="logout">
                  <q-item-section avatar><q-icon name="logout" /></q-item-section>
                  <q-item-section>Abmelden</q-item-section>
                </q-item>
              </q-list>
            </q-menu>
          </q-btn>
        </div>
      </div>
    </q-drawer>

    <div v-if="showFloatingControls" class="app-floating-controls" aria-label="Schnellaktionen">
      <q-btn
        v-if="showFloatingNavigation"
        id="app-navigation-toggle"
        class="app-floating-control app-floating-control--flat"
        round
        flat
        icon="menu"
        :aria-label="navigationLabel"
        aria-controls="app-navigation-drawer"
        :aria-expanded="drawerOpen"
        @click="openNavigation"
      >
        <q-badge
          v-if="notificationBadgeCount"
          floating
          rounded
          :color="attentionCount ? 'warning' : 'primary'"
          :text-color="attentionCount ? 'dark' : 'white'"
          :label="notificationBadgeLabel"
          aria-hidden="true"
        />
      </q-btn>
      <q-space />
      <q-btn
        v-if="showFloatingWorkspace"
        id="workspace-explorer-toggle"
        class="app-floating-control"
        round
        unelevated
        icon="folder_open"
        aria-label="Datei-Explorer öffnen"
        :aria-expanded="workspace.open"
        aria-controls="workspace-file-panel"
        @click="toggleWorkspace"
      >
        <q-tooltip>Datei-Explorer öffnen</q-tooltip>
      </q-btn>
    </div>

    <NotificationCenterDialog
      v-model="notificationDialogOpen"
      @navigated="closeDrawerOnMobile"
    />

    <q-dialog v-model="renameDialogOpen">
      <q-card class="session-rename-dialog app-surface">
        <q-form @submit.prevent="renameSession">
          <q-card-section>
            <div class="text-h6">Session umbenennen</div>
          </q-card-section>
          <q-card-section class="q-pt-none">
            <q-input
              v-model="renameTitle"
              outlined
              autofocus
              label="Titel"
              maxlength="240"
              lazy-rules
              :rules="[requiredRule]"
            />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md q-pt-none">
            <q-btn flat no-caps label="Abbrechen" :disable="renaming" v-close-popup />
            <q-btn
              unelevated
              no-caps
              color="primary"
              label="Speichern"
              type="submit"
              :loading="renaming"
            />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog
      v-if="route.name === 'chat' && $q.screen.lt.md"
      :model-value="workspace.open"
      maximized
      transition-show="slide-left"
      transition-hide="slide-right"
      @update:model-value="setWorkspaceOpen"
    >
      <q-card id="workspace-file-panel" class="app-file-dialog">
        <FileExplorerPanel />
      </q-card>
    </q-dialog>

    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import AppLogo from 'components/AppLogo.vue'
import FileExplorerPanel from 'components/FileExplorerPanel.vue'
import NotificationCenterDialog from 'components/NotificationCenterDialog.vue'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useApprovalStore } from 'stores/approvals'
import { useAuthStore } from 'stores/auth'
import { useChatStore } from 'stores/chat'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useQuestionStore } from 'stores/questions'
import { useWorkspaceStore } from 'stores/workspace'
import { useConfirm } from 'src/composables/confirm'
import { groupSessionsByRecency, sessionSidebarStatus } from 'src/utils/session-groups'

defineOptions({ name: 'MainLayout' })

const ACTIVE_RUN_STATUSES = new Set(['queued', 'running', 'waiting_approval', 'waiting_input'])
const SESSION_ACTIVITY_REFRESH_MS = 5000

const drawerOpen = ref(true)
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const identity = useAgentIdentityStore()
const chat = useChatStore()
const workspace = useWorkspaceStore()
const { confirm } = useConfirm()
const approvals = useApprovalStore()
const notifications = useInAppNotificationStore()
const questions = useQuestionStore()
const sessionInfiniteScroll = ref(null)
const groupedSessions = computed(() => groupSessionsByRecency(chat.sessions))
const sessionStatuses = computed(() => new Map(chat.sessions.map((session) => [
  session.id,
  sessionSidebarStatus(
    session,
    approvals.forSession(session.id).length,
    questions.forSession(session.id).length
  )
])))
const showFloatingNavigation = computed(() => !drawerOpen.value)
const showFloatingWorkspace = computed(() => route.name === 'chat' && !workspace.open)
const showFloatingControls = computed(() => (
  showFloatingNavigation.value || showFloatingWorkspace.value
))
const attentionCount = computed(() => approvals.pending.length + questions.pending.length)
const notificationBadgeCount = computed(() => attentionCount.value || notifications.unreadCount)
const notificationBadgeLabel = computed(() => notificationBadgeCount.value > 99
  ? '99+'
  : String(notificationBadgeCount.value || ''))
const notificationBellLabel = computed(() => {
  const parts = []
  if (attentionCount.value) {
    parts.push(`${attentionCount.value} ${attentionCount.value === 1 ? 'Aktion' : 'Aktionen'} erforderlich`)
  }
  if (notifications.unreadCount) {
    parts.push(`${notifications.unreadCount} ungelesene ${notifications.unreadCount === 1 ? 'Benachrichtigung' : 'Benachrichtigungen'}`)
  }
  return parts.length ? `Benachrichtigungen öffnen, ${parts.join(', ')}` : 'Benachrichtigungen öffnen'
})
const navigationLabel = computed(() => {
  if (!attentionCount.value && !notifications.unreadCount) return 'Navigation öffnen'
  return `Navigation öffnen, ${notificationBellLabel.value.replace('Benachrichtigungen öffnen, ', '')}`
})
const notificationDialogOpen = ref(false)
const renameDialogOpen = ref(false)
const renameSessionId = ref(null)
const renameTitle = ref('')
const renaming = ref(false)
const pinningSessionId = ref(null)
const openActionMenuSessionId = ref(null)
let sessionActivityTimer = null
let sessionActivityRefreshPending = false
const visualViewportHeight = ref(null)
const visualViewportOffsetTop = ref(0)
const visualViewportStyle = computed(() => {
  if (route.name !== 'chat' || visualViewportHeight.value === null) return null
  return {
    '--app-visual-viewport-height': `${visualViewportHeight.value}px`,
    '--app-visual-viewport-offset-top': `${visualViewportOffsetTop.value}px`
  }
})
const userInitials = computed(() => {
  const localPart = (auth.username || '').trim().split('@')[0]
  const parts = localPart.split(/[\s._-]+/).filter(Boolean)
  if (!parts.length) return '?'
  const first = parts[0][0]
  const last = parts.length > 1 ? parts[parts.length - 1][0] : ''
  return `${first}${last}`.toUpperCase()
})
const userMenuLabel = computed(() => 'Benutzermenü öffnen')

function requiredRule(value) {
  return Boolean(String(value || '').trim()) || 'Pflichtfeld'
}

function updateVisualViewport() {
  const viewport = window.visualViewport
  const height = viewport?.height || window.innerHeight
  const offsetTop = viewport?.offsetTop || 0
  if (Number.isFinite(height) && height > 0) visualViewportHeight.value = height
  visualViewportOffsetTop.value = Number.isFinite(offsetTop) ? Math.max(0, offsetTop) : 0
}

function openRenameDialog(session) {
  renameSessionId.value = session.id
  renameTitle.value = session.title
  renameDialogOpen.value = true
}

async function renameSession() {
  const title = renameTitle.value.trim()
  if (!renameSessionId.value || !title) return

  renaming.value = true
  try {
    await chat.renameSession(renameSessionId.value, title)
    renameDialogOpen.value = false
  } catch {
    // The central API interceptor displays the error; the dialog remains open for correction.
  } finally {
    renaming.value = false
  }
}

async function toggleSessionPinned(session) {
  if (pinningSessionId.value) return

  pinningSessionId.value = session.id
  try {
    await chat.setSessionPinned(session.id, !session.pinned)
  } catch {
    // The central API interceptor displays the error; existing state remains unchanged.
  } finally {
    pinningSessionId.value = null
  }
}

async function openNewSession() {
  if (!await confirmDiscardedEdit()) return
  if ($q.screen.lt.md) drawerOpen.value = false
  await chat.loadSession(null)
  await router.push({ name: 'chat' })
}

async function openSession(id) {
  if ($q.screen.lt.md) drawerOpen.value = false
  await router.push({ name: 'chat', query: { session: id } })
}

function closeDrawerOnMobile() {
  if ($q.screen.lt.md) drawerOpen.value = false
}

function openNavigation() {
  drawerOpen.value = true
}

function openNotifications() {
  if ($q.screen.lt.md) drawerOpen.value = false
  notificationDialogOpen.value = true
}

async function closeNavigation() {
  drawerOpen.value = false
  await nextTick()
  document.getElementById('app-navigation-toggle')?.focus()
}

function refreshSessionsWhenVisible() {
  if (document.visibilityState !== 'visible') return
  void chat.refreshSessions().catch(() => {})
  void approvals.fetchPending({ suppressErrorToast: true }).catch(() => {})
  void questions.fetchPending({ suppressErrorToast: true }).catch(() => {})
  void notifications.fetchLatest({ suppressErrorToast: true }).catch(() => {})
}

async function refreshActiveSessions() {
  if (
    document.visibilityState !== 'visible'
    || sessionActivityRefreshPending
  ) return
  sessionActivityRefreshPending = true
  try {
    const requests = [approvals.fetchPending({ suppressErrorToast: true })]
    if (chat.sessions.some((session) => ACTIVE_RUN_STATUSES.has(session.activeRunStatus))) {
      requests.push(chat.refreshSessions({ suppressErrorToast: true }))
    }
    await Promise.all(requests)
  } catch {
    // The next interval reconciles transient background refresh failures.
  } finally {
    sessionActivityRefreshPending = false
  }
}

async function loadMoreSessions(_index, done) {
  try {
    await chat.fetchMoreSessions()
    done(!chat.sessionsNextCursor)
  } catch {
    done(true)
  }
}

async function retrySessionPagination() {
  chat.retrySessionPagination()
  await nextTick()
  sessionInfiniteScroll.value?.resume()
}

function closeActionMenu(id) {
  if (openActionMenuSessionId.value === id) openActionMenuSessionId.value = null
}

async function removeSession(id) {
  if (!await confirmDiscardedEdit()) return
  await chat.deleteSession(id)
  if (route.query.session === id) await router.push({ name: 'chat' })
}

async function logout() {
  if (!await confirmDiscardedEdit()) return
  await auth.logout()
  await router.replace({ name: 'login' })
}

async function confirmDiscardedEdit() {
  if (!workspace.editorDirty) return true
  const accepted = await confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
  if (accepted) workspace.discardEdit()
  return accepted
}

async function toggleWorkspace() {
  if (workspace.open && !await confirmDiscardedEdit()) return
  workspace.toggle()
}

async function setWorkspaceOpen(open) {
  if (open) {
    workspace.show()
    return
  }
  if (await confirmDiscardedEdit()) workspace.open = false
}

watch(
  () => [workspace.open, $q.screen.lt.lg],
  ([open, narrow]) => {
    if (open && narrow) drawerOpen.value = false
  }
)

watch(() => route.name, (name) => {
  if (name !== 'chat') workspace.open = false
})

onBeforeRouteUpdate(confirmDiscardedEdit)
onBeforeRouteLeave(confirmDiscardedEdit)

onMounted(async () => {
  updateVisualViewport()
  window.visualViewport?.addEventListener('resize', updateVisualViewport)
  window.visualViewport?.addEventListener('scroll', updateVisualViewport)
  window.addEventListener('resize', updateVisualViewport)
  document.addEventListener('visibilitychange', refreshSessionsWhenVisible)
  sessionActivityTimer = window.setInterval(refreshActiveSessions, SESSION_ACTIVITY_REFRESH_MS)
  await Promise.allSettled([
    chat.fetchSessions(),
    approvals.fetchPending(),
    questions.fetchPending(),
    notifications.fetchLatest({ suppressErrorToast: true }),
    identity.fetchIdentity()
  ])
  await nextTick()
  sessionInfiniteScroll.value?.resume()
})

onBeforeUnmount(() => {
  window.visualViewport?.removeEventListener('resize', updateVisualViewport)
  window.visualViewport?.removeEventListener('scroll', updateVisualViewport)
  window.removeEventListener('resize', updateVisualViewport)
  document.removeEventListener('visibilitychange', refreshSessionsWhenVisible)
  window.clearInterval(sessionActivityTimer)
  sessionActivityTimer = null
})
</script>
