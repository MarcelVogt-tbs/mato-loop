<template>
  <section class="chat-home q-mx-auto" aria-label="Home">
    <header class="text-center q-px-md q-pb-sm">
      <AppLogo :size="36" />
      <h1 class="text-h5 text-weight-bold q-mt-md q-mb-sm">{{ greeting }}</h1>
      <p class="text-body1 app-muted q-mt-none q-mb-md">Was möchtest du tun?</p>
    </header>
    <div class="q-pa-md">
      <q-banner v-if="failedSources.length" rounded class="app-surface-tonal q-mb-md" role="status">
        Nicht aktuell verfügbar: {{ failedSources.join(', ') }}.
        <template #action><q-btn flat no-caps label="Erneut laden" :loading="refreshing" @click="refresh" /></template>
      </q-banner>
      <q-item v-if="notificationCount" clickable v-ripple class="app-surface-tonal rounded-borders q-mb-lg" @click="controlCenter.openApp('notifications').catch(() => {})">
        <q-item-section side class="q-pr-sm"><q-icon name="notifications" color="primary" /></q-item-section>
        <q-item-section>
          <q-item-label class="text-weight-medium">Benachrichtigungen</q-item-label>
          <q-item-label caption>{{ notificationSummary }}</q-item-label>
        </q-item-section>
        <q-item-section side><q-icon name="chevron_right" /></q-item-section>
      </q-item>

      <section v-if="$q.screen.gt.xs" aria-labelledby="home-sessions">
        <h2 id="home-sessions" class="text-subtitle1 text-weight-medium q-mt-none q-mb-xs">Zuletzt aktiv</h2>
        <div v-if="home.loading && !home.loaded" aria-label="Sessions werden geladen">
          <q-skeleton v-for="index in 3" :key="index" type="text" class="q-my-md" />
        </div>
        <p v-else-if="home.loadFailed" class="app-muted">Sessions konnten nicht geladen werden.</p>
        <q-list v-else-if="recentSessions.length">
          <q-item v-for="session in recentSessions" :key="session.id" clickable v-ripple :active="false" :to="sessionRoute(session)" class="rounded-borders q-px-sm q-py-sm">
            <q-item-section side class="q-pr-sm"><q-icon name="chat_bubble_outline" color="primary" size="20px" /></q-item-section>
            <q-item-section>
              <q-item-label lines="1" :class="{ 'text-weight-medium': session.hasUnreadMessages }">
                {{ session.title }}<span v-if="session.hasUnreadMessages" class="text-primary text-caption q-ml-xs">Neue Antwort</span>
              </q-item-label>
              <q-item-label caption>{{ projectName(session.projectId) }} · {{ formatRelativeDateTime(session.lastMessageAt || session.createdAt) }}</q-item-label>
              <q-item-label v-if="session.status" caption class="text-primary">{{ session.status }}</q-item-label>
            </q-item-section>
            <q-item-section side class="q-pl-sm"><q-icon name="chevron_right" /></q-item-section>
          </q-item>
        </q-list>
        <p v-else class="app-muted q-mt-sm">Deine letzten Gespräche erscheinen hier.</p>
      </section>
      <section v-if="ready(automations) && upcoming.length" class="q-mt-lg" aria-labelledby="home-automations">
        <h2 id="home-automations" class="text-subtitle2 app-muted q-mt-none q-mb-xs">Nächste Automationen</h2>
        <q-list>
          <q-item v-for="automation in upcoming" :key="automation.id" clickable v-ripple :to="{ name: 'settings-automation-detail', params: { automationId: automation.id } }" class="rounded-borders q-px-sm">
            <q-item-section side class="q-pr-sm"><q-icon name="auto_awesome" color="primary" size="20px" /></q-item-section>
            <q-item-section>
              <q-item-label lines="1">{{ automation.name }}</q-item-label>
              <q-item-label caption>{{ formatAbsoluteDateTime(automation.effectiveState.nextRunAt) }}</q-item-label>
            </q-item-section>
            <q-item-section side class="q-pl-sm"><q-icon name="chevron_right" /></q-item-section>
          </q-item>
        </q-list>
      </section>
      <div v-if="$q.screen.gt.xs && ready(computers) && computers.items.length" class="q-mt-lg row items-center">
        <q-btn
          v-for="computer in computers.items"
          :key="computer.id"
          flat dense rounded no-caps no-wrap
          class="col-shrink q-px-sm q-mr-xs q-mb-xs text-caption"
          :class="chat.activeContext.preferredExecutionAgentId === computer.id ? 'app-surface-tonal' : 'app-muted'"
          :disable="computerSelectionDisabled"
          :aria-label="`${computer.alias}: ${computer.connected ? 'Online' : 'Offline'}. Computer auswählen`"
          :aria-pressed="chat.activeContext.preferredExecutionAgentId === computer.id"
          @click="selectComputer(computer)"
        >
          <q-icon name="computer" :color="computer.connected ? 'positive' : 'negative'" size="18px" class="q-mr-xs" aria-hidden="true" />
          <span class="ellipsis">{{ computer.alias }}</span>
        </q-btn>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import AppLogo from 'src/shared/ui/AppLogo.vue'
import { useApprovalStore } from 'stores/approvals'
import { useAutomationStore } from 'stores/automations'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useProjectStore } from 'stores/projects'
import { useQuestionStore } from 'stores/questions'
import { useUserProfileStore } from 'stores/user-profile'
import { formatAbsoluteDateTime, formatRelativeDateTime } from 'src/utils/date-time'
import { sessionSidebarStatus } from 'src/features/chat/session-groups'
import { useHomeStore } from './store'

const props = defineProps({ computerSelectionDisabled: Boolean })
const home = useHomeStore()
const chat = useChatStore()
const projects = useProjectStore()
const automations = useAutomationStore()
const controlCenter = useControlCenterStore()
const computers = useExecutionAgentStore()
const approvals = useApprovalStore()
const questions = useQuestionStore()
const notifications = useInAppNotificationStore()
const profile = useUserProfileStore()
const refreshing = ref(false)
const ready = (store) => store.loaded && !store.loadFailed
const greeting = computed(() => {
  const name = ready(profile) && [profile.preferredName, profile.fullName]
    .flatMap((field) => [field.privateOnly, field.both, field.publicOnly])
    .find((value) => value?.trim())?.trim()
  return name ? `Willkommen zurück, ${name}` : 'Willkommen zurück'
})
const attentionCount = computed(() => (ready(approvals) ? approvals.pending.length : 0) + (ready(questions) ? questions.pending.length : 0))
const unreadCount = computed(() => ready(notifications) ? notifications.unreadCount : 0)
const notificationCount = computed(() => attentionCount.value + unreadCount.value)
const notificationSummary = computed(() => [
  attentionCount.value ? `${attentionCount.value} offene ${attentionCount.value === 1 ? 'Aufgabe' : 'Aufgaben'}` : null,
  unreadCount.value ? `${unreadCount.value} ungelesene ${unreadCount.value === 1 ? 'Benachrichtigung' : 'Benachrichtigungen'}` : null
].filter(Boolean).join(' · '))
const failedSources = computed(() => [
  ['Sessions', home], ['Projekte', projects], ['Automationen', automations],
  ['Computer', computers], ['Freigaben', approvals], ['Rückfragen', questions], ['Benachrichtigungen', notifications], ['Profil', profile]
].filter(([, store]) => store.loadFailed).map(([label]) => label))
const recentSessions = computed(() => home.sessions.map((session) => ({
  ...session,
  status: sessionSidebarStatus(session, approvals.forSession(session.id).length, questions.forSession(session.id).length)?.label
})))
const upcoming = computed(() => automations.items
  .filter((item) => item.effectiveState?.enabled && item.effectiveState.nextRunAt)
  .sort((left, right) => new Date(left.effectiveState.nextRunAt) - new Date(right.effectiveState.nextRunAt))
  .slice(0, 2))
const projectName = (id) => id ? projects.items.find((item) => item.id === id)?.name || 'Projekt' : 'Ohne Projekt'
const sessionRoute = (session) => ({ name: 'chat', query: { session: session.id, ...(session.projectId ? { project: session.projectId } : {}) } })

function selectComputer(computer) {
  if (props.computerSelectionDisabled || chat.activeContext.preferredExecutionAgentId === computer.id) return
  void chat.updateActive({
    preferred_execution_agent_id: computer.id,
    current_directory: computer.homeDirectory || null
  })
}

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await Promise.allSettled([
      home.load(), projects.fetchAll(), automations.fetchAutomations(),
      computers.fetchAll(), approvals.fetchPending(), questions.fetchPending(), notifications.fetchLatest(), profile.fetchProfile()
    ])
  } finally {
    refreshing.value = false
  }
}

onMounted(() => { void Promise.allSettled([home.load(), profile.fetchProfile()]) })
</script>

<style scoped>
.chat-home {
  width: min(900px, 100%);
  margin-top: clamp(24px, 6vh, 72px);
  margin-bottom: 24px;
}
</style>
