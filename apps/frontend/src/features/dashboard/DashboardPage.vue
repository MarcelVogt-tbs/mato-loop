<template>
  <q-page class="app-page dashboard-page q-mx-auto">
    <AppPageHeader :title="greeting" subtitle="Mach dort weiter, wo du zuletzt aufgehört hast.">
      <template #actions>
        <q-btn flat no-caps icon="refresh" label="Aktualisieren" aria-label="Dashboard aktualisieren" :loading="refreshing" @click="refresh" />
        <q-btn unelevated no-caps color="primary" icon="add" label="Neue Session" :to="{ name: 'chat' }" />
      </template>
    </AppPageHeader>

    <q-banner v-if="failedSources.length" rounded class="app-surface-tonal q-mb-lg" role="status">
      Nicht aktuell verfügbar: {{ failedSources.join(', ') }}.
      <template #action><q-btn flat no-caps label="Erneut laden" :loading="refreshing" @click="refresh" /></template>
    </q-banner>

    <div class="row q-col-gutter-md">
      <div class="col-12 col-md-8">
        <q-card flat class="app-surface" tag="section" aria-labelledby="dashboard-sessions">
          <q-card-section>
            <h2 id="dashboard-sessions" class="text-h6 q-my-none">Zuletzt aktiv</h2>
            <p class="text-body2 app-muted q-mt-xs q-mb-none">Deine letzten Gespräche aus allen Projekten.</p>
          </q-card-section>
          <q-separator />
          <q-card-section v-if="dashboard.loading && !dashboard.loaded" aria-label="Sessions werden geladen">
            <q-skeleton v-for="index in 3" :key="index" type="text" class="q-my-md" />
          </q-card-section>
          <q-card-section v-else-if="dashboard.loadFailed" class="app-muted">Sessions konnten nicht geladen werden.</q-card-section>
          <q-list v-else-if="dashboard.sessions.length" padding>
            <q-item v-for="session in recentSessions" :key="session.id" clickable v-ripple :to="sessionRoute(session)" class="q-py-sm">
              <q-item-section side class="q-pr-sm">
                <q-icon name="chat_bubble_outline" color="primary" />
              </q-item-section>
              <q-item-section>
                <q-item-label lines="2" :class="{ 'text-weight-bold': session.hasUnreadMessages }">
                  {{ session.title }}<span v-if="session.hasUnreadMessages" class="text-primary text-caption q-ml-xs">Neue Antwort</span>
                </q-item-label>
                <q-item-label caption>{{ projectName(session.projectId) }} · {{ formatRelativeDateTime(session.lastMessageAt || session.createdAt) }}</q-item-label>
                <q-item-label v-if="session.status" caption class="text-primary">{{ session.status }}</q-item-label>
              </q-item-section>
              <q-item-section side class="q-pl-sm"><q-icon name="chevron_right" /></q-item-section>
            </q-item>
          </q-list>
          <q-card-section v-else class="text-center q-py-xl">
            <q-icon name="forum" size="40px" color="primary" class="q-mb-sm" />
            <p class="text-subtitle1 q-mb-xs">Platz für deine nächste Idee</p>
            <p class="app-muted q-mb-md">Starte eine Session, um mit deinem Agenten zusammenzuarbeiten.</p>
            <q-btn outline no-caps color="primary" label="Erste Session starten" :to="{ name: 'chat' }" />
          </q-card-section>
        </q-card>
      </div>

      <div class="col-12 col-md-4">
        <q-card flat class="app-surface" tag="section" aria-labelledby="dashboard-status">
          <q-card-section>
            <h2 id="dashboard-status" class="text-h6 q-my-none">Kurzüberblick</h2>
          </q-card-section>
          <q-separator />
          <q-list padding>
            <q-item clickable v-ripple :to="{ name: 'chat', query: { controlCenter: 'notifications' } }" class="q-py-sm">
              <q-item-section side class="q-pr-sm"><q-icon name="pending_actions" :color="attentionReady && attentionCount ? 'warning' : 'primary'" /></q-item-section>
              <q-item-section>
                <q-item-label>{{ attentionReady ? (attentionCount ? `${attentionCount} offene ${attentionCount === 1 ? 'Anfrage' : 'Anfragen'}` : 'Keine offenen Anfragen') : 'Offene Anfragen: –' }}</q-item-label>
                <q-item-label caption>Freigaben und Rückfragen</q-item-label>
              </q-item-section>
              <q-item-section side class="q-pl-sm"><q-icon name="chevron_right" /></q-item-section>
            </q-item>
            <q-item clickable v-ripple :to="{ name: 'settings-execution-agents' }" class="q-py-sm">
              <q-item-section side class="q-pr-sm"><q-icon name="computer" color="primary" /></q-item-section>
              <q-item-section>
                <q-item-label>{{ computersReady ? `${onlineComputers} von ${computers.items.length} Computern online` : 'Computer online: –' }}</q-item-label>
                <q-item-label caption>Computer verwalten</q-item-label>
              </q-item-section>
              <q-item-section side class="q-pl-sm"><q-icon name="chevron_right" /></q-item-section>
            </q-item>
          </q-list>
        </q-card>
        <q-card v-if="ready(automations) && upcoming.length" flat class="app-surface q-mt-md" tag="section" aria-labelledby="dashboard-automations">
          <q-card-section><h2 id="dashboard-automations" class="text-subtitle1 text-weight-medium q-my-none">Nächste Automationen</h2></q-card-section>
          <q-separator />
          <q-list padding>
            <q-item v-for="automation in upcoming" :key="automation.id" clickable v-ripple :to="{ name: 'settings-automation-detail', params: { automationId: automation.id } }">
              <q-item-section>
                <q-item-label lines="2">{{ automation.name }}</q-item-label>
                <q-item-label caption>{{ formatAbsoluteDateTime(automation.effectiveState.nextRunAt) }}</q-item-label>
              </q-item-section>
              <q-item-section side class="q-pl-sm"><q-icon name="chevron_right" /></q-item-section>
            </q-item>
          </q-list>
        </q-card>
      </div>
    </div>
  </q-page>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AppPageHeader from 'src/shared/ui/AppPageHeader.vue'
import { useApprovalStore } from 'stores/approvals'
import { useAutomationStore } from 'stores/automations'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useProjectStore } from 'stores/projects'
import { useQuestionStore } from 'stores/questions'
import { useUserProfileStore } from 'stores/user-profile'
import { formatAbsoluteDateTime, formatRelativeDateTime } from 'src/utils/date-time'
import { sessionSidebarStatus } from 'src/features/chat/session-groups'
import { useDashboardStore } from './store'

defineOptions({ name: 'DashboardPage' })

const dashboard = useDashboardStore()
const projects = useProjectStore()
const automations = useAutomationStore()
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
const computersReady = computed(() => ready(computers))
const onlineComputers = computed(() => computers.items.filter((item) => item.connected).length)
const attentionReady = computed(() => ready(approvals) && ready(questions))
const attentionCount = computed(() => approvals.pending.length + questions.pending.length)
const failedSources = computed(() => [
  ['Sessions', dashboard], ['Projekte', projects], ['Automationen', automations],
  ['Computer', computers], ['Freigaben', approvals], ['Rückfragen', questions], ['Profil', profile]
].filter(([, store]) => store.loadFailed).map(([label]) => label))
const recentSessions = computed(() => dashboard.sessions.map((session) => ({
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
      dashboard.load(), projects.fetchAll(), automations.fetchAutomations(),
      computers.fetchAll(), approvals.fetchPending(), questions.fetchPending(), profile.fetchProfile()
    ])
  } finally {
    refreshing.value = false
  }
}

onMounted(() => {
  void Promise.allSettled([dashboard.load(), profile.fetchProfile()])
  computers.startPolling()
})
onBeforeUnmount(() => computers.stopPolling())
</script>

<style scoped>
.dashboard-page {
  max-width: 1440px;
}

:global(.app-shell--navigation-overlay) .dashboard-page {
  padding-top: max(72px, calc(24px + env(safe-area-inset-top)));
}
</style>
