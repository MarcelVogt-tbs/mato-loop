<template>
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

        <q-btn flat round dense icon="search" aria-label="Suche öffnen" @click="search.openDialog">
          <q-tooltip>Sessions und Projekte durchsuchen</q-tooltip>
        </q-btn>

        <q-btn flat round dense icon="menu" aria-label="Navigation schließen" @click="closeNavigation" />
      </q-toolbar>
      <q-separator class="lt-md" />

      <div class="col column no-wrap drawer-navigation q-py-sm">
        <q-scroll-area
          class="col session-scroll-area"
          :content-style="{ width: '100%' }"
          :content-active-style="{ width: '100%' }"
          visible
          tabindex="0"
          aria-label="Projekte und Sessions"
        >
          <section aria-labelledby="projects-heading">
            <q-item
              clickable dense tag="div" class="q-px-sm q-py-xs"
              aria-controls="project-list" :aria-expanded="projectsSectionOpen"
              @click="projectsSectionOpen = !projectsSectionOpen"
            >
              <q-item-section>
                <q-item-label
                  id="projects-heading" header class="q-pa-none text-subtitle2 text-weight-medium"
                  :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                >
                  Projekte
                  <q-icon :name="projectsSectionOpen ? 'expand_more' : 'chevron_right'" size="18px" />
                </q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn
                  flat round dense size="sm" icon="create_new_folder"
                  class="drawer-section__action" aria-label="Projekt erstellen"
                  @click.stop="openProjectDialog('create')"
                >
                  <q-tooltip>Projekt erstellen</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>

            <q-list id="project-list" v-show="projectsSectionOpen" class="project-list">
              <div v-if="projects.loading && !projects.loaded" class="row justify-center q-pa-sm" aria-label="Projekte werden geladen">
                <q-spinner-dots color="primary" size="24px" />
              </div>
              <div v-else-if="projects.loadFailed && !projects.loaded" class="q-pa-sm">
                <q-btn
                  flat dense no-caps class="full-width" color="primary" icon-right="refresh"
                  label="Projekte erneut laden" @click="loadProjects"
                />
              </div>
              <q-expansion-item
                v-for="project in projects.items"
                :key="project.id"
                v-model="expandedProjects[project.id]"
                dense
                hide-expand-icon
                class="project-list-item rounded-borders q-my-xs"
                :class="{
                  'app-surface-tonal': sessionDropTarget === project.id,
                  'project-list-item--menu-open': openProjectActionMenuId === project.id
                }"
                header-class="project-list-item__header q-px-sm"
                @dragover="dragSessionOver($event, project.id)"
                @dragleave="leaveSessionDropTarget"
                @drop="dropSession($event, project.id)"
                @show="loadProjectSessions(project.id)"
              >
                <template #header>
                  <q-item-section avatar class="project-list-item__icon">
                    <q-icon :name="expandedProjects[project.id] ? mdiFolderOpenOutline : mdiFolderOutline" size="22px" />
                  </q-item-section>
                  <q-item-section>
                    <q-item-label lines="1" class="text-body2 text-weight-medium">{{ project.name }}</q-item-label>
                  </q-item-section>
                  <q-item-section side class="project-list-item__actions row no-wrap items-center">
                    <q-btn
                      flat round dense size="sm" icon="add_comment"
                      class="project-list-item__action"
                      :aria-label="`Neue Session in ${project.name}`"
                      @click.stop="openNewSession(project.id)"
                    >
                      <q-tooltip>Neue Session</q-tooltip>
                    </q-btn>
                    <SidebarActionMenu
                      :model-value="openProjectActionMenuId === project.id"
                      class="project-list-item__action"
                      :aria-label="`Aktionen für ${project.name}`"
                      @update:model-value="setProjectActionMenu(project.id, $event)"
                    >
                      <q-item clickable v-ripple v-close-popup @click="openProjectDialog('edit', project)">
                        <q-item-section avatar><q-icon name="edit" /></q-item-section>
                        <q-item-section>Projekt bearbeiten</q-item-section>
                      </q-item>
                      <q-separator />
                      <q-item
                        clickable v-ripple v-close-popup class="text-negative"
                        @click="deleteProject(project)"
                      >
                        <q-item-section avatar><q-icon name="delete_outline" /></q-item-section>
                        <q-item-section>Projekt löschen</q-item-section>
                      </q-item>
                    </SidebarActionMenu>
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
                  @drag-start="startSessionDrag"
                  @drag-end="endSessionDrag"
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

          <section
            class="q-mt-md"
            :class="{ 'app-surface-tonal': sessionDropTarget === null }"
            aria-labelledby="sessions-heading"
            @dragover="dragSessionOver($event, null)"
            @dragleave="leaveSessionDropTarget"
            @drop="dropSession($event, null)"
          >
            <q-item dense tag="div" class="q-px-sm q-py-xs">
              <q-item-section
                role="button" tabindex="0" aria-controls="standalone-session-list"
                :aria-expanded="sessionsSectionOpen"
                @click="sessionsSectionOpen = !sessionsSectionOpen"
                @keydown.enter="sessionsSectionOpen = !sessionsSectionOpen"
                @keydown.space.prevent="sessionsSectionOpen = !sessionsSectionOpen"
              >
                <q-item-label
                  id="sessions-heading" header class="q-pa-none text-subtitle2 text-weight-medium"
                  :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
                >
                  Sessions
                  <q-icon :name="sessionsSectionOpen ? 'expand_more' : 'chevron_right'" size="18px" />
                </q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-btn
                  flat round dense size="sm" icon="add_comment" class="drawer-section__action"
                  aria-label="Neue Session" @click.stop="openNewSession(null)"
                >
                  <q-tooltip>Neue Session</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>
            <SidebarSessionList
              id="standalone-session-list"
              v-show="sessionsSectionOpen"
              :list="standaloneSessionList"
              :active-session-id="routeSessionId"
              :statuses="sessionStatuses"
              :pinning-session-id="pinningSessionId"
              show-groups
              @open="openSession"
              @rename="openRenameDialog"
              @move="openMoveDialog"
              @drag-start="startSessionDrag"
              @drag-end="endSessionDrag"
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
          id="user-menu-trigger" unelevated no-caps class="full-width app-user-menu-trigger"
          :aria-label="userMenuLabel"
        >
          <div class="row no-wrap items-center full-width">
            <q-avatar color="primary" text-color="white" size="36px">{{ userInitials }}</q-avatar>
            <div class="col ellipsis text-left text-body2 q-ml-md">
              {{ auth.username || 'Benutzer' }}
            </div>
            <q-icon name="keyboard_arrow_up" size="20px" class="q-ml-sm" />
          </div>

          <q-menu anchor="top right" self="bottom right" :offset="[0, 8]" class="app-user-popup app-action-menu">
            <q-list padding>
              <q-item clickable v-ripple to="/settings" v-close-popup @click="closeDrawerOnMobile">
                <q-item-section avatar><q-icon name="tune" /></q-item-section>
                <q-item-section>Einstellungen</q-item-section>
              </q-item>
              <q-item v-if="auth.isAdmin" clickable v-ripple :to="{ name: 'admin-users' }" v-close-popup @click="closeDrawerOnMobile">
                <q-item-section avatar><q-icon name="admin_panel_settings" /></q-item-section>
                <q-item-section>Administration</q-item-section>
              </q-item>
              <q-item clickable v-ripple v-close-popup @click="openAccountDialog">
                <q-item-section avatar><q-icon name="manage_accounts" /></q-item-section>
                <q-item-section>Konto</q-item-section>
              </q-item>
              <q-item clickable v-ripple v-close-popup @click="$q.dark.toggle()">
                <q-item-section avatar><q-icon :name="$q.dark.isActive ? 'light_mode' : 'dark_mode'" /></q-item-section>
                <q-item-section>{{ $q.dark.isActive ? 'Heller Modus' : 'Dunkler Modus' }}</q-item-section>
              </q-item>
              <q-item
                clickable v-ripple href="/docs/" target="_blank" rel="noopener noreferrer" v-close-popup
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

  <AccountDialog
    v-model="accountDialogOpen"
    :before-password-change="confirmDiscardedEdit"
    @closed="focusUserMenuTrigger"
  />
  <SearchDialog @open-project="openSearchProject" @open-session="openSearchSession" />
  <ProjectDialog
    v-model="projectDialogOpen"
    v-model:name="projectName"
    v-model:instructions="projectInstructions"
    v-model:execution-agent-id="projectDefaultExecutionAgentId"
    v-model:current-directory="projectDefaultCurrentDirectory"
    v-model:directory-picker-open="projectDirectoryPickerOpen"
    :mode="projectDialogMode"
    :saving="projectSaving"
    :execution-agent-options="projectDialogOpen ? projectExecutionAgentOptions : []"
    :agents-loading="executionAgents.loading"
    :selected-agent="projectSelectedExecutionAgent"
    :directory-picker-available="projectDirectoryPickerAvailable"
    :required-rule="requiredRule"
    @select-agent="selectProjectExecutionAgent"
    @save="saveProject"
  />
  <SessionDialogs
    v-model:rename-open="renameDialogOpen"
    v-model:rename-title="renameTitle"
    v-model:move-open="moveDialogOpen"
    v-model:move-project-id="moveProjectId"
    :renaming="renaming"
    :moving-session="movingSession"
    :move-source-project-id="moveSourceProjectId"
    :project-options="projectOptions"
    :required-rule="requiredRule"
    @rename="renameSession"
    @move="moveSession"
  />
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { mdiFolderOpenOutline, mdiFolderOutline } from '@quasar/extras/mdi-v7'
import { useRoute, useRouter } from 'vue-router'
import AccountDialog from 'src/features/auth/AccountDialog.vue'
import AppLogo from 'src/shared/ui/AppLogo.vue'
import ProjectDialog from 'src/features/projects/ProjectDialog.vue'
import SearchDialog from 'src/features/search/SearchDialog.vue'
import SidebarActionMenu from 'src/features/chat/components/SidebarActionMenu.vue'
import SidebarSessionList from 'src/features/chat/components/SidebarSessionList.vue'
import SessionDialogs from 'src/features/chat/components/SessionDialogs.vue'
import { useProjectNavigation } from 'src/features/projects/useProjectNavigation'
import { useSidebarSessions } from 'src/features/chat/useSidebarSessions'
import { useApprovalStore } from 'stores/approvals'
import { useAutomationStore } from 'stores/automations'
import { useAuthStore } from 'stores/auth'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useQuestionStore } from 'stores/questions'
import { useSearchStore } from 'stores/search'
import { useProjectStore } from 'stores/projects'
import { useWorkspaceStore } from 'stores/workspace'
import { useConfirm } from 'src/composables/confirm'

const props = defineProps({ confirmDiscardedEdit: { type: Function, required: true } })
const drawerOpen = defineModel({ type: Boolean, required: true })
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const executionAgents = useExecutionAgentStore()
const workspace = useWorkspaceStore()
const approvals = useApprovalStore()
const automations = useAutomationStore()
const notifications = useInAppNotificationStore()
const questions = useQuestionStore()
const search = useSearchStore()
const projects = useProjectStore()
const { confirm } = useConfirm()
const selectedProjectId = computed(() => (
  typeof route.query.project === 'string' ? route.query.project : null
))
const routeSessionId = computed(() => (
  typeof route.query.session === 'string' ? route.query.session : null
))
const sessionsSectionOpen = ref(true)
const accountDialogOpen = ref(false)
const {
  projectOptions, standaloneSessionList, sessionStatuses,
  renameDialogOpen, renameTitle, renaming, pinningSessionId,
  moveDialogOpen, moveSourceProjectId, moveProjectId, movingSession,
  sessionDropTarget, openRenameDialog, projectSessionList, loadProjectSessions,
  loadStandaloneSessions, startSessionDrag, endSessionDrag, dragSessionOver,
  leaveSessionDropTarget, dropSession, openMoveDialog, moveSession,
  renameSession, toggleSessionPinned, openNewSession, openSession,
  openSearchProject, openSearchSession, loadMoreSessions,
  retrySessionPagination, removeSession
} = useSidebarSessions({
  chat, projects, approvals, questions, workspace, controlCenter, automations,
  route, router, $q, confirm, drawerOpen, selectedProjectId, confirmDiscardedEdit: props.confirmDiscardedEdit
})
const {
  projectsSectionOpen, expandedProjects, openProjectActionMenuId,
  projectDialogOpen, projectDialogMode, projectName, projectInstructions,
  projectDefaultExecutionAgentId, projectDefaultCurrentDirectory,
  projectDirectoryPickerOpen, projectSaving, projectSelectedExecutionAgent,
  projectExecutionAgentOptions, projectDirectoryPickerAvailable,
  loadProjects, openProjectDialog, selectProjectExecutionAgent,
  setProjectActionMenu, saveProject, deleteProject
} = useProjectNavigation({
  projects, executionAgents, chat, controlCenter, workspace, automations,
  notifications, router, $q, confirm, selectedProjectId,
  confirmDiscardedEdit: props.confirmDiscardedEdit, openNewSession
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

async function closeNavigation() {
  drawerOpen.value = false
  await nextTick()
  document.getElementById('app-navigation-toggle')?.focus()
}

async function logout() {
  if (!await props.confirmDiscardedEdit()) return
  await auth.logout()
}

watch(selectedProjectId, (projectId, previousProjectId) => {
  if (projectId) {
    projectsSectionOpen.value = true
    expandedProjects[projectId] = true
  } else if (previousProjectId) {
    sessionsSectionOpen.value = true
  }
  void chat.setSessionScope(projectId)
    .then(() => chat.fetchDefaults(projectId))
    .catch(() => {})
}, { immediate: true })

onMounted(async () => {
  await Promise.allSettled([loadProjects(), loadStandaloneSessions()])
})
</script>
