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
            @click="openControlCenterApp('notifications')"
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
          <q-scroll-area
            class="col session-scroll-area"
            visible
            tabindex="0"
            aria-label="Projekte und Sessions"
          >
            <section aria-labelledby="projects-heading">
              <q-item
                clickable
                dense
                tag="div"
                class="q-px-md q-py-xs"
                aria-controls="project-list"
                :aria-expanded="projectsSectionOpen"
                @click="projectsSectionOpen = !projectsSectionOpen"
              >
                <q-item-section>
                  <q-item-label
                    id="projects-heading"
                    header
                    class="q-pa-none text-subtitle2 text-weight-medium"
                    :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                  >
                    Projekte
                    <q-icon :name="projectsSectionOpen ? 'expand_more' : 'chevron_right'" size="18px" />
                  </q-item-label>
                </q-item-section>
                <q-item-section side>
                  <q-btn
                    flat
                    round
                    dense
                    size="sm"
                    icon="create_new_folder"
                    class="drawer-section__action"
                    aria-label="Projekt erstellen"
                    @click.stop="openProjectDialog('create')"
                  >
                    <q-tooltip>Projekt erstellen</q-tooltip>
                  </q-btn>
                </q-item-section>
              </q-item>

              <q-list id="project-list" v-show="projectsSectionOpen" class="project-list q-px-sm">
                <div v-if="projects.loading && !projects.loaded" class="row justify-center q-pa-sm" aria-label="Projekte werden geladen">
                  <q-spinner-dots color="primary" size="24px" />
                </div>
                <div v-else-if="projects.loadFailed && !projects.loaded" class="q-pa-sm">
                  <q-btn
                    flat
                    dense
                    no-caps
                    class="full-width"
                    color="primary"
                    icon-right="refresh"
                    label="Projekte erneut laden"
                    @click="loadProjects"
                  />
                </div>
                <q-expansion-item
                  v-for="project in projects.items"
                  :key="project.id"
                  v-model="expandedProjects[project.id]"
                  hide-expand-icon
                  class="project-list-item rounded-borders q-my-xs"
                  header-class="project-list-item__header"
                  @show="loadProjectSessions(project.id)"
                >
                  <template #header>
                    <q-item-section avatar>
                      <q-icon :name="expandedProjects[project.id] ? mdiFolderOpenOutline : mdiFolderOutline" size="22px" />
                    </q-item-section>
                    <q-item-section>
                      <q-item-label lines="1" class="text-body2 text-weight-medium">{{ project.name }}</q-item-label>
                    </q-item-section>
                    <q-item-section side class="project-list-item__actions row no-wrap items-center">
                      <q-btn
                        flat
                        round
                        dense
                        size="sm"
                        icon="add_comment"
                        class="project-list-item__action"
                        :aria-label="`Neue Session in ${project.name}`"
                        @click.stop="openNewSession(project.id)"
                      >
                        <q-tooltip>Neue Session</q-tooltip>
                      </q-btn>
                      <q-btn
                        flat
                        round
                        dense
                        size="sm"
                        icon="more_horiz"
                        class="project-list-item__action"
                        :aria-label="`Aktionen für ${project.name}`"
                        @click.stop
                      >
                        <q-menu class="app-action-menu">
                          <q-list padding>
                            <q-item clickable v-ripple v-close-popup @click="openProjectDialog('rename', project)">
                              <q-item-section avatar><q-icon name="edit" /></q-item-section>
                              <q-item-section>Umbenennen</q-item-section>
                            </q-item>
                            <q-item
                              clickable
                              v-ripple
                              v-close-popup
                              class="text-negative"
                              :disable="project.sessionCount > 0"
                              @click="deleteProject(project)"
                            >
                              <q-item-section avatar><q-icon name="delete_outline" /></q-item-section>
                              <q-item-section>Leeres Projekt löschen</q-item-section>
                            </q-item>
                          </q-list>
                        </q-menu>
                      </q-btn>
                    </q-item-section>
                  </template>
                  <SidebarSessionList
                    :list="projectSessionList(project.id)"
                    :active-session-id="routeSessionId"
                    :statuses="sessionStatuses"
                    :pinning-session-id="pinningSessionId"
                    nested
                    @open="openSession"
                    @rename="openRenameDialog"
                    @move="openMoveDialog"
                    @delete="removeSession"
                    @toggle-pin="toggleSessionPinned"
                    @load-more="loadMoreSessions(project.id)"
                    @retry-more="retrySessionPagination(project.id)"
                    @retry="loadProjectSessions(project.id, true)"
                  />
                </q-expansion-item>
                <q-item v-if="projects.loaded && !projects.items.length" dense class="q-px-sm">
                  <q-item-section><q-item-label caption>Noch keine Projekte</q-item-label></q-item-section>
                </q-item>
              </q-list>
            </section>

            <section class="q-mt-md" aria-labelledby="sessions-heading">
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
                    aria-label="Neue Session"
                    @click="openNewSession(null)"
                  >
                    <q-tooltip>Neue Session</q-tooltip>
                  </q-btn>
                </q-item-section>
              </q-item>
              <SidebarSessionList
                :list="standaloneSessionList"
                :active-session-id="routeSessionId"
                :statuses="sessionStatuses"
                :pinning-session-id="pinningSessionId"
                show-groups
                @open="openSession"
                @rename="openRenameDialog"
                @move="openMoveDialog"
                @delete="removeSession"
                @toggle-pin="toggleSessionPinned"
                @load-more="loadMoreSessions(null)"
                @retry-more="retrySessionPagination(null)"
                @retry="loadStandaloneSessions(true)"
              />
            </section>
          </q-scroll-area>
        </div>

        <div class="drawer-user-area q-pa-sm">
          <q-btn
            id="user-menu-trigger"
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
                <q-item clickable v-ripple to="/settings" v-close-popup @click="closeDrawerOnMobile">
                  <q-item-section avatar><q-icon name="tune" /></q-item-section>
                  <q-item-section>Einstellungen</q-item-section>
                </q-item>
                <q-item clickable v-ripple v-close-popup @click="openAccountDialog">
                  <q-item-section avatar><q-icon name="manage_accounts" /></q-item-section>
                  <q-item-section>Konto</q-item-section>
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
        v-if="showFloatingControlCenter"
        id="control-center-toggle"
        class="app-floating-control"
        round
        unelevated
        icon="apps"
        aria-label="Control Center öffnen"
        :aria-expanded="controlCenter.open"
        aria-controls="control-center-panel"
        @click="openControlCenter"
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
        <q-tooltip>Control Center öffnen</q-tooltip>
      </q-btn>
    </div>

    <AccountDialog
      v-model="accountDialogOpen"
      :before-password-change="confirmDiscardedEdit"
      @closed="focusUserMenuTrigger"
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

    <q-dialog v-model="projectDialogOpen">
      <q-card class="session-rename-dialog app-surface">
        <q-form @submit.prevent="saveProject">
          <q-card-section>
            <div class="text-h6">{{ projectDialogMode === 'create' ? 'Projekt erstellen' : 'Projekt umbenennen' }}</div>
          </q-card-section>
          <q-card-section class="q-pt-none">
            <q-input
              v-model="projectName"
              outlined
              autofocus
              label="Name"
              maxlength="240"
              lazy-rules
              :rules="[requiredRule]"
            />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md q-pt-none">
            <q-btn flat no-caps label="Abbrechen" :disable="projectSaving" v-close-popup />
            <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="projectSaving" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog v-model="moveDialogOpen">
      <q-card class="session-rename-dialog app-surface">
        <q-form @submit.prevent="moveSession">
          <q-card-section>
            <div class="text-h6">Session verschieben</div>
          </q-card-section>
          <q-card-section class="q-pt-none">
            <q-select
              v-model="moveProjectId"
              outlined
              emit-value
              map-options
              label="Ziel"
              :options="projectOptions"
            />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md q-pt-none">
            <q-btn flat no-caps label="Abbrechen" :disable="movingSession" v-close-popup />
            <q-btn
              unelevated
              no-caps
              color="primary"
              label="Verschieben"
              type="submit"
              :loading="movingSession"
              :disable="moveProjectId === moveSourceProjectId"
            />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog
      v-if="route.name === 'chat' && $q.screen.lt.md"
      :model-value="controlCenter.open"
      aria-label="Control Center"
      maximized
      transition-show="slide-left"
      transition-hide="slide-right"
      @update:model-value="setControlCenterOpen"
    >
      <q-card class="app-file-dialog">
        <ControlCenterPanel />
      </q-card>
    </q-dialog>

    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { mdiFolderOpenOutline, mdiFolderOutline } from '@quasar/extras/mdi-v7'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import AccountDialog from 'components/AccountDialog.vue'
import AppLogo from 'components/AppLogo.vue'
import ControlCenterPanel from 'components/ControlCenterPanel.vue'
import SidebarSessionList from 'components/SidebarSessionList.vue'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useApprovalStore } from 'stores/approvals'
import { useAutomationStore } from 'stores/automations'
import { useAuthStore } from 'stores/auth'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useQuestionStore } from 'stores/questions'
import { useProjectStore } from 'stores/projects'
import { useWorkspaceStore } from 'stores/workspace'
import { useConfirm } from 'src/composables/confirm'
import { notifyError } from 'src/utils/notifications'
import { sessionSidebarStatus } from 'src/utils/session-groups'

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
const controlCenter = useControlCenterStore()
const workspace = useWorkspaceStore()
const { confirm } = useConfirm()
const approvals = useApprovalStore()
const automations = useAutomationStore()
const notifications = useInAppNotificationStore()
const questions = useQuestionStore()
const projects = useProjectStore()
const projectsSectionOpen = ref(true)
const expandedProjects = reactive({})
const selectedProjectId = computed(() => (
  typeof route.query.project === 'string' ? route.query.project : null
))
const routeSessionId = computed(() => (
  typeof route.query.session === 'string' ? route.query.session : null
))
const projectOptions = computed(() => [
  { label: 'Ohne Projekt', value: null },
  ...projects.items.map((project) => ({ label: project.name, value: project.id }))
])
const standaloneSessionList = computed(() => chat.sessionList(null) || emptySidebarList)
const sessionStatuses = computed(() => new Map(chat.cachedSessions.map((session) => [
  session.id,
  sessionSidebarStatus(
    session,
    approvals.forSession(session.id).length,
    questions.forSession(session.id).length
  )
])))
const showFloatingNavigation = computed(() => !drawerOpen.value)
const showFloatingControlCenter = computed(() => route.name === 'chat' && !controlCenter.open)
const showFloatingControls = computed(() => (
  showFloatingNavigation.value || showFloatingControlCenter.value
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
const renameDialogOpen = ref(false)
const renameSessionId = ref(null)
const renameTitle = ref('')
const renaming = ref(false)
const pinningSessionId = ref(null)
const projectDialogOpen = ref(false)
const projectDialogMode = ref('create')
const projectDialogTarget = ref(null)
const projectName = ref('')
const projectSaving = ref(false)
const moveDialogOpen = ref(false)
const moveSessionId = ref(null)
const moveSourceProjectId = ref(null)
const moveProjectId = ref(null)
const movingSession = ref(false)
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
const accountDialogOpen = ref(false)
const emptySidebarList = Object.freeze({
  items: [],
  loaded: false,
  loading: false,
  loadFailed: false,
  nextCursor: null,
  loadingMore: false,
  loadMoreFailed: false
})

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

function chatQuery(sessionId = null, projectId = selectedProjectId.value, extra = {}) {
  return {
    ...(projectId ? { project: projectId } : {}),
    ...(sessionId ? { session: sessionId } : {}),
    ...extra
  }
}

function projectSessionList(projectId) {
  return chat.sessionList(projectId) || emptySidebarList
}

async function loadProjectSessions(projectId, force = false) {
  const list = chat.ensureSessionList(projectId)
  if (!force && (list.loaded || list.loading)) return
  try {
    await chat.fetchSessions(null, projectId)
  } catch {
    // The central API interceptor displays the error and the folder exposes a retry action.
  }
}

async function loadStandaloneSessions(force = false) {
  const list = chat.ensureSessionList(null)
  if (!force && (list.loaded || list.loading)) return
  try {
    await chat.fetchSessions(null, null)
  } catch {
    // The central API interceptor displays the error and the list exposes a retry action.
  }
}

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
  projectName.value = mode === 'rename' ? project?.name || '' : ''
  projectDialogOpen.value = true
}

async function saveProject() {
  const name = projectName.value.trim()
  if (!name) return
  projectSaving.value = true
  try {
    if (projectDialogMode.value === 'create') {
      const project = await projects.create(name)
      projectDialogOpen.value = false
      $q.notify({ type: 'positive', message: 'Projekt erstellt.' })
      expandedProjects[project.id] = true
      await openNewSession(project.id)
    } else if (projectDialogTarget.value) {
      await projects.update(projectDialogTarget.value.id, name)
      projectDialogOpen.value = false
      $q.notify({ type: 'positive', message: 'Projekt umbenannt.' })
    }
  } catch {
    // The central API interceptor displays the error; the dialog remains open.
  } finally {
    projectSaving.value = false
  }
}

async function deleteProject(project) {
  if (!project || project.sessionCount > 0) return
  const accepted = await confirm(
    `Projekt „${project.name}“ löschen?`,
    'Leeres Projekt löschen?',
    { okLabel: 'Löschen' }
  )
  if (!accepted) return
  try {
    await projects.remove(project.id)
    delete expandedProjects[project.id]
    $q.notify({ type: 'positive', message: 'Projekt gelöscht.' })
    if (selectedProjectId.value === project.id) {
      try {
        await chat.setSessionScope(null)
      } catch {
        // Navigation still leaves the deleted scope; the standalone list keeps its retry state.
      }
      await router.push({ name: 'chat' })
    }
    chat.removeProjectScope(project.id)
  } catch {
    // The central API interceptor displays the error.
  }
}

function openMoveDialog(session) {
  moveSessionId.value = session.id
  moveSourceProjectId.value = session.projectId || null
  moveProjectId.value = moveSourceProjectId.value
  moveDialogOpen.value = true
}

async function moveSession() {
  if (!moveSessionId.value || moveProjectId.value === moveSourceProjectId.value) return
  const sessionId = moveSessionId.value
  const targetProjectId = moveProjectId.value || null
  if (route.query.session === sessionId && !await confirmDiscardedEdit()) return
  movingSession.value = true
  try {
    await chat.moveSession(sessionId, targetProjectId)
    moveDialogOpen.value = false
    $q.notify({ type: 'positive', message: 'Session verschoben.' })
    if (route.query.session === sessionId) {
      await router.push({
        name: 'chat',
        query: {
          ...(targetProjectId ? { project: targetProjectId } : {}),
          session: sessionId
        }
      })
    }
    void projects.fetchAll().catch(() => {})
  } catch {
    // The central API interceptor displays the error; the dialog remains open.
  } finally {
    movingSession.value = false
  }
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

async function openNewSession(projectId = null) {
  if (!await confirmDiscardedEdit()) return
  if ($q.screen.lt.md) drawerOpen.value = false
  try {
    await chat.setSessionScope(projectId)
  } catch {
    // The new-session draft remains usable while the sidebar offers a list retry.
  }
  await chat.loadSession(null)
  await router.push({ name: 'chat', query: chatQuery(null, projectId) })
}

async function openSession(session) {
  if ($q.screen.lt.md) drawerOpen.value = false
  await router.push({
    name: 'chat',
    query: chatQuery(session.id, session.projectId || null)
  })
}

function closeDrawerOnMobile() {
  if ($q.screen.lt.md) drawerOpen.value = false
}

function openAccountDialog() {
  closeDrawerOnMobile()
  accountDialogOpen.value = true
}

async function focusUserMenuTrigger() {
  await nextTick()
  document.getElementById(drawerOpen.value ? 'user-menu-trigger' : 'app-navigation-toggle')?.focus()
}

function openNavigation() {
  drawerOpen.value = true
}

async function openControlCenterApp(appId) {
  if ($q.screen.lt.md) drawerOpen.value = false
  if (route.name !== 'chat') {
    await router.push({
      name: 'chat',
      query: chatQuery(chat.current?.id, selectedProjectId.value, { controlCenter: appId })
    })
    return
  }
  try {
    await controlCenter.openApp(appId)
  } catch {
    // The central API interceptor displays the error and restores the previous tabs.
  }
}

async function closeNavigation() {
  drawerOpen.value = false
  await nextTick()
  document.getElementById('app-navigation-toggle')?.focus()
}

function refreshSessionsWhenVisible() {
  if (document.visibilityState !== 'visible') return
  if (projects.loadFailed) void loadProjects()
  for (const [scope, list] of Object.entries(chat.sessionLists)) {
    if (list.loaded) void chat.refreshSessions(null, scope === 'standalone' ? null : scope).catch(() => {})
  }
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
    const requests = [
      approvals.fetchPending({ suppressErrorToast: true }),
      questions.fetchPending({ suppressErrorToast: true })
    ]
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

async function loadMoreSessions(projectId) {
  try {
    await chat.fetchMoreSessions(null, projectId)
  } catch {
    // The central API interceptor displays the error and the list exposes a retry action.
  }
}

async function retrySessionPagination(projectId) {
  chat.retrySessionPagination(projectId)
  await loadMoreSessions(projectId)
}

async function removeSession(session) {
  const id = typeof session === 'string' ? session : session.id
  const sourceProjectId = typeof session === 'string'
    ? chat.findCachedSession(id)?.projectId || null
    : session.projectId || null
  let impact
  try {
    impact = await chat.sessionDeleteImpact(id, { suppressErrorToast: true })
  } catch (error) {
    notifyError(error)
    return
  }
  let automationMode = null
  if (impact.requiredAutomationCount) {
    const count = impact.automationCount
    const accepted = await confirm(
      `Beim Fortfahren werden ${count} verknüpfte Automation${count === 1 ? '' : 'en'} samt Historie dauerhaft gelöscht.`,
      'Abhängige Automationen löschen?',
      { okLabel: 'Alles löschen' }
    )
    if (!accepted) return
    automationMode = 'deleteAutomations'
  } else if (impact.automationCount) {
    const count = impact.automationCount
    const accepted = await confirm(
      `Die Session ist Ausgabeziel von ${count} Automation${count === 1 ? '' : 'en'}. Verknüpfung lösen und Session löschen?`,
      'Verknüpfte Session löschen?',
      { okLabel: 'Lösen und löschen' }
    )
    if (!accepted) return
    automationMode = 'unlink'
  }
  if (workspace.sessionId === id && !await confirmDiscardedEdit({ discard: false })) return
  try {
    await chat.deleteSession(id, {
      automationMode,
      automationImpact: automationMode ? impact.automationImpact : null
    })
    if (sourceProjectId) void projects.fetchAll().catch(() => {})
  } catch {
    return
  }
  if (workspace.sessionId === id) workspace.discardEdit()
  if (automationMode) void automations.fetchAutomations().catch(() => {})
  if (route.query.session === id) {
    await router.push({ name: 'chat', query: chatQuery(null, sourceProjectId) })
  }
}

async function logout() {
  if (!await confirmDiscardedEdit()) return
  await auth.logout()
}

async function confirmDiscardedEdit({ discard = true } = {}) {
  if (!workspace.editorDirty) return true
  const accepted = await confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
  if (accepted && discard) workspace.discardEdit()
  return accepted
}

async function openControlCenter() {
  try {
    await controlCenter.openApp(controlCenter.activeContentId || 'files')
  } catch {
    // The central API interceptor displays the error and restores the previous tabs.
  }
}

async function setControlCenterOpen(open) {
  if (!open && !await confirmDiscardedEdit()) return
  try {
    await controlCenter.setOpen(open)
  } catch {
    // The central API interceptor displays the error and restores the previous state.
  }
}

watch(
  () => [controlCenter.open, $q.screen.lt.lg],
  ([open, narrow]) => {
    if (open && narrow) drawerOpen.value = false
  }
)

watch(
  selectedProjectId,
  (projectId) => {
    if (projectId) {
      projectsSectionOpen.value = true
      expandedProjects[projectId] = true
    }
    void chat.setSessionScope(projectId).catch(() => {})
  },
  { immediate: true }
)

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
    loadProjects(),
    loadStandaloneSessions(),
    approvals.fetchPending(),
    questions.fetchPending(),
    notifications.fetchLatest({ suppressErrorToast: true }),
    identity.fetchIdentity()
  ])
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
