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
      <q-item v-if="attentionReady && attentionCount" clickable v-ripple class="app-surface-tonal rounded-borders q-mb-lg" @click="controlCenter.openApp('notifications').catch(() => {})">
        <q-item-section side class="q-pr-sm"><q-icon name="pending_actions" color="warning" /></q-item-section>
        <q-item-section>
          <q-item-label class="text-weight-medium">Braucht deine Aufmerksamkeit</q-item-label>
          <q-item-label caption>{{ attentionCount }} offene {{ attentionCount === 1 ? 'Anfrage' : 'Anfragen' }} · Freigaben und Rückfragen</q-item-label>
        </q-item-section>
        <q-item-section side><q-icon name="chevron_right" /></q-item-section>
      </q-item>

      <section aria-labelledby="home-sessions">
        <div class="row items-center justify-between q-mb-xs">
          <h2 id="home-sessions" class="text-subtitle1 text-weight-medium q-my-none">Zuletzt aktiv</h2>
          <q-btn flat dense no-caps icon="refresh" label="Aktualisieren" aria-label="Home aktualisieren" :loading="refreshing" @click="refresh" />
        </div>
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
      <div class="q-mt-lg">
        <q-btn flat dense no-caps icon="computer" class="app-muted text-caption" :label="ready(computers) ? `${onlineComputers} von ${computers.items.length} Computern online` : 'Computer online: –'" :to="{ name: 'settings-execution-agents' }" />
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import AppLogo from 'src/shared/ui/AppLogo.vue'
import { useApprovalStore } from 'stores/approvals'
import { useAutomationStore } from 'stores/automations'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useProjectStore } from 'stores/projects'
import { useQuestionStore } from 'stores/questions'
import { useUserProfileStore } from 'stores/user-profile'
import { formatAbsoluteDateTime, formatRelativeDateTime } from 'src/utils/date-time'
import { sessionSidebarStatus } from 'src/features/chat/session-groups'
import { useHomeStore } from './store'

const home = useHomeStore()
const projects = useProjectStore()
const automations = useAutomationStore()
const controlCenter = useControlCenterStore()
const computers = useExecutionAgentStore()
const approvals = useApprovalStore()
const questions = useQuestionStore()
const profile = useUserProfileStore()
const refreshing = ref(false)
const ready = (store) => store.loaded && !store.loadFailed
const greeting = computed(() => {
  const name = ready(profile) && [profile.preferredName, profile.fullName]
    .flatMap((field) => [field.privateOnly, field.both, field.publicOnly])
    .find((value) => value?.trim())?.trim()
  return name ? `Willkommen zurück, ${name}` : 'Willkommen zurück'
})
const onlineComputers = computed(() => computers.items.filter((item) => item.connected).length)
const attentionReady = computed(() => ready(approvals) && ready(questions))
const attentionCount = computed(() => approvals.pending.length + questions.pending.length)
const failedSources = computed(() => [
  ['Sessions', home], ['Projekte', projects], ['Automationen', automations],
  ['Computer', computers], ['Freigaben', approvals], ['Rückfragen', questions], ['Profil', profile]
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

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await Promise.allSettled([
      home.load(), projects.fetchAll(), automations.fetchAutomations(),
      computers.fetchAll(), approvals.fetchPending(), questions.fetchPending(), profile.fetchProfile()
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
