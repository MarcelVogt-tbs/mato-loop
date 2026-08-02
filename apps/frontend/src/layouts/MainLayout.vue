<template>
  <q-layout
    view="hHh Lpr fFf"
    class="app-shell"
    :class="{ 'app-shell--chat': route.name === 'chat' }"
    :style="visualViewportStyle"
  >
    <q-header class="app-header text-dark">
      <q-toolbar class="app-toolbar">
        <div class="app-toolbar__drawer-controls row no-wrap items-center">
          <router-link to="/" class="app-brand row no-wrap items-center q-gutter-sm gt-sm">
            <AppLogo />
            <div>
              <div class="app-brand__name">MatoLoop</div>
              <div class="app-brand__tagline gt-xs">Personal Agent Workspace</div>
            </div>
          </router-link>

          <q-space class="gt-sm" />

          <q-btn
            flat
            round
            dense
            icon="menu"
            aria-label="Navigation umschalten"
            @click="drawerOpen = !drawerOpen"
          />
        </div>

        <q-space />

        <q-chip v-if="approvals.pending.length" dense square color="warning" text-color="dark" icon="pending_actions">
          {{ approvals.pending.length }} Freigaben
        </q-chip>
        <q-btn
          flat
          round
          dense
          icon="folder_open"
          aria-label="Datei-Explorer öffnen"
          :class="{ 'app-header-action--active': workspace.open }"
          @click="workspace.show()"
        >
          <q-tooltip>Datei-Explorer öffnen</q-tooltip>
        </q-btn>
      </q-toolbar>
    </q-header>

    <q-drawer
      v-model="drawerOpen"
      show-if-above
      :width="288"
      :breakpoint="1024"
      class="app-drawer"
    >
      <div class="column full-height no-wrap drawer-layout">
        <q-toolbar class="lt-md q-px-sm">
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
            icon="close"
            aria-label="Navigation schließen"
            @click="drawerOpen = false"
          />
        </q-toolbar>
        <q-separator class="lt-md" />

        <div class="col drawer-navigation q-py-sm">
          <section class="drawer-section drawer-section--sessions" aria-labelledby="sessions-heading">
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

            <div class="drawer-section__body">
              <q-list id="session-list" class="session-list q-px-sm q-pb-sm">
                <q-item v-if="!chat.sessions.length" dense class="q-px-sm q-py-xs">
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
                    :active="route.query.session === session.id"
                    :active-class="$q.dark.isActive ? 'bg-grey-9 text-white' : 'bg-grey-3 text-dark'"
                    @click="openSession(session.id)"
                  >
                    <q-item-section class="session-list-item__content">
                      <q-item-label
                        lines="1"
                        class="session-list-item__title text-body2"
                        :class="{ 'text-weight-medium': route.query.session === session.id }"
                      >
                        {{ session.title }}
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
                        <div v-if="session.workingDirectory" class="q-mt-xs text-grey-4">
                          {{ session.workingDirectory }}
                        </div>
                      </q-tooltip>
                    </q-item-section>
                    <q-item-section side class="session-list-item__actions row no-wrap items-center self-center q-pl-xs">
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
              <div v-if="hasSessionOverflow" class="q-px-md q-pb-sm">
                <q-btn
                  flat
                  dense
                  no-caps
                  class="full-width"
                  :color="$q.dark.isActive ? 'grey-5' : 'grey-7'"
                  :label="sessionsExpanded ? 'Weniger anzeigen' : 'Mehr anzeigen'"
                  :aria-expanded="sessionsExpanded"
                  aria-controls="session-list"
                  @click="sessionsExpanded = !sessionsExpanded"
                />
              </div>
            </div>
          </section>

        </div>

        <div class="drawer-user-area q-pa-sm">
          <q-btn
            unelevated
            no-caps
            class="full-width app-user-menu-trigger"
            aria-label="Benutzermenü öffnen"
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
                <q-item clickable v-ripple v-close-popup @click="$q.dark.toggle()">
                  <q-item-section avatar>
                    <q-icon :name="$q.dark.isActive ? 'light_mode' : 'dark_mode'" />
                  </q-item-section>
                  <q-item-section>
                    {{ $q.dark.isActive ? 'Heller Modus' : 'Dunkler Modus' }}
                  </q-item-section>
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

    <q-drawer
      v-model="workspace.open"
      side="right"
      overlay
      bordered
      :width="$q.screen.lt.sm ? $q.screen.width : 420"
      class="app-file-drawer"
    >
      <FileExplorerPanel />
    </q-drawer>

    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import AppLogo from 'components/AppLogo.vue'
import FileExplorerPanel from 'components/FileExplorerPanel.vue'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useApprovalStore } from 'stores/approvals'
import { useAuthStore } from 'stores/auth'
import { useChatStore } from 'stores/chat'
import { useWorkspaceStore } from 'stores/workspace'
import { groupSessionsByRecency } from 'src/utils/session-groups'

defineOptions({ name: 'MainLayout' })

const SESSION_PREVIEW_LIMIT = 10

const drawerOpen = ref(true)
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const identity = useAgentIdentityStore()
const chat = useChatStore()
const workspace = useWorkspaceStore()
const approvals = useApprovalStore()
const sessionsExpanded = ref(false)
const visibleSessions = computed(() => (
  sessionsExpanded.value ? chat.sessions : chat.sessions.slice(0, SESSION_PREVIEW_LIMIT)
))
const hasSessionOverflow = computed(() => chat.sessions.length > SESSION_PREVIEW_LIMIT)
const groupedSessions = computed(() => groupSessionsByRecency(visibleSessions.value))
const renameDialogOpen = ref(false)
const renameSessionId = ref(null)
const renameTitle = ref('')
const renaming = ref(false)
const pinningSessionId = ref(null)
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

function refreshSessionsWhenVisible() {
  if (document.visibilityState !== 'visible') return
  void chat.fetchSessions().catch(() => {})
}

async function removeSession(id) {
  await chat.deleteSession(id)
  if (route.query.session === id) await router.push({ name: 'chat' })
}

async function logout() {
  await auth.logout()
  await router.replace({ name: 'login' })
}

watch(() => workspace.open, (open) => {
  if (open && $q.screen.lt.md) drawerOpen.value = false
})

onMounted(async () => {
  updateVisualViewport()
  window.visualViewport?.addEventListener('resize', updateVisualViewport)
  window.visualViewport?.addEventListener('scroll', updateVisualViewport)
  window.addEventListener('resize', updateVisualViewport)
  document.addEventListener('visibilitychange', refreshSessionsWhenVisible)
  await Promise.all([chat.fetchSessions(), approvals.fetchPending(), identity.fetchIdentity()])
})

onBeforeUnmount(() => {
  window.visualViewport?.removeEventListener('resize', updateVisualViewport)
  window.visualViewport?.removeEventListener('scroll', updateVisualViewport)
  window.removeEventListener('resize', updateVisualViewport)
  document.removeEventListener('visibilitychange', refreshSessionsWhenVisible)
})
</script>
