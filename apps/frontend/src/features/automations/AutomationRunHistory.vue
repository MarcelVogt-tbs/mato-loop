<template>
  <div class="q-py-md" :aria-busy="loading">
    <q-banner v-if="loadFailed" class="bg-negative text-white q-mb-md">
      Der Verlauf oder der ausgewählte Lauf konnte nicht geladen werden.
      <template #action><q-btn flat no-caps label="Erneut laden" @click="refresh()" /></template>
    </q-banner>
    <section v-if="selectedRun" aria-label="Ausgewählter Lauf" class="q-mb-md">
      <q-banner v-if="selectedRun.error" class="bg-negative text-white q-mb-sm">{{ apiErrorMessage(selectedRun.error, 'Der Automationslauf ist fehlgeschlagen.') }}</q-banner>
      <div class="text-body2 q-mt-md">
        <q-icon :name="runStatus(selectedRun).icon" :color="runStatus(selectedRun).color" class="q-mr-xs" />
        {{ runStatus(selectedRun).label }} · {{ formatAbsoluteDateTime(selectedRun.createdAt) }} · {{ triggerLabel(selectedRun.trigger) }}
      </div>
      <div v-if="selectedRun.scheduledFor" class="text-caption app-muted">Geplant für {{ formatAbsoluteDateTime(selectedRun.scheduledFor) }}</div>
      <q-btn v-if="selectedRun.messageId" flat no-caps color="primary" icon="forum" label="Im Gespräch öffnen" :to="{ name: 'chat', query: { session: sessionId, message: selectedRun.messageId } }" />
      <q-expansion-item dense label="Details zur Ausführung" class="q-mt-md">
        <div class="q-pa-sm q-gutter-sm text-body2">
          <div class="text-caption app-muted">Revision {{ selectedRun.automationRevision }}</div>
          <div v-if="selectedRun.definition?.name" class="text-weight-medium">{{ selectedRun.definition.name }}</div>
          <div class="text-pre-wrap result-content">{{ selectedRun.definition?.task }}</div>
          <div v-if="selectedRun.definition?.skill?.name">Skill: {{ selectedRun.definition.skill.name }}</div>
          <div v-for="(snapshot, index) in selectedRun.scheduleSnapshots || []" :key="index">
            {{ scheduleSummary(scheduleDraft(snapshot)) }}
            <span v-if="snapshot.validUntil"> · Bis {{ formatAbsoluteDateTime(snapshot.validUntil) }}</span>
            <span v-if="snapshot.enabled === false"> · Deaktiviert</span>
          </div>
          <div v-for="item in runtimeItems" :key="item.label" class="text-pre-wrap result-content">{{ item.label }}: {{ item.value }}</div>
          <div class="text-caption app-muted">Gestartet: {{ formatAbsoluteDateTime(selectedRun.startedAt) || 'Noch nicht' }} · Beendet: {{ formatAbsoluteDateTime(selectedRun.completedAt) || 'Noch nicht' }}</div>
        </div>
      </q-expansion-item>
    </section>
    <q-separator v-if="selectedRun" />
    <q-list v-if="runs.length" separator dense aria-label="Automationsläufe">
      <q-item v-for="run in runs" :key="run.id" clickable :active="selectedRun?.id === run.id" :aria-current="selectedRun?.id === run.id ? 'true' : undefined" @click="selectRun(run.id)">
        <q-item-section>
          <q-item-label><q-icon :name="runStatus(run).icon" :color="runStatus(run).color" class="q-mr-xs" />{{ runStatus(run).label }} · {{ formatAbsoluteDateTime(run.createdAt) }}</q-item-label>
          <q-item-label caption>{{ triggerLabel(run.trigger) }}</q-item-label>
        </q-item-section>
        <q-item-section side><q-icon name="chevron_right" /></q-item-section>
      </q-item>
    </q-list>
    <p v-else-if="loaded && !loading && !loadFailed" class="text-body2 app-muted">Noch keine Läufe.</p>
    <div class="row justify-center q-mt-md">
      <q-spinner v-if="loading" color="primary" size="24px" aria-label="Verlauf wird geladen" />
      <q-btn v-else-if="automations.hasMore" flat no-caps color="primary" label="Weitere Läufe laden" @click="loadMore" />
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAutomationStore } from 'stores/automations'
import { apiErrorMessage } from 'src/utils/api-errors'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { scheduleDraft, scheduleSummary } from 'src/utils/automation-schedules'

const props = defineProps({ automationId: { type: String, required: true }, sessionId: { type: String, required: true }, runId: { type: String, default: '' }, visible: Boolean })
const automations = useAutomationStore()
const route = useRoute()
const router = useRouter()
const selectedRun = ref(null)
const loading = ref(false)
const loaded = ref(false)
const loadFailed = ref(false)
let token = 0
let alive = true
let timer
const runs = computed(() => automations.runs.filter((run) => run.automationId === props.automationId))
const activeRun = computed(() => ['queued', 'running', 'waiting_approval', 'waiting_input'].includes(selectedRun.value?.status))
const runtimeItems = computed(() => {
  const runtime = selectedRun.value?.definition?.runtime || {}
  const labels = { full: 'Vollständig', read_only: 'Nur lesen', disabled: 'Deaktiviert', required: 'Erforderlich', available: 'Verfügbar' }
  return [['Modell', runtime.modelId], ['Memory', runtime.memoryMode], ['Websuche', runtime.webSearchPolicy], ['Computer-ID', runtime.preferredExecutionAgentId], ['Aktueller Ordner', runtime.currentDirectory]]
    .filter(([, value]) => value).map(([label, value]) => ({ label, value: labels[value] || value }))
})

function runStatus(run) {
  return ({
    queued: { label: 'Eingereiht', color: 'primary', icon: 'schedule' },
    running: { label: 'Läuft', color: 'primary', icon: 'play_circle' },
    waiting_approval: { label: 'Wartet auf Freigabe', color: 'warning', icon: 'pending_actions' },
    waiting_input: { label: 'Wartet auf Eingabe', color: 'warning', icon: 'help_outline' },
    completed: { label: 'Erledigt', color: 'positive', icon: 'check_circle' },
    failed: { label: 'Fehlgeschlagen', color: 'negative', icon: 'error' },
    cancelled: { label: 'Abgebrochen', color: 'grey-7', icon: 'cancel' },
    skipped: { label: 'Übersprungen', color: 'grey-7', icon: 'skip_next' }
  })[run?.status] || { label: 'Unbekannt', color: 'grey-7', icon: 'help_outline' }
}

function triggerLabel(trigger) {
  return ({ manual: 'Manuell', scheduled: 'Zeitplan', agent_response: 'Agent hat geantwortet', agent_error: 'Agent-Auftrag fehlgeschlagen' })[trigger] || 'Unbekannt'
}

function runVersion(run) {
  return JSON.stringify([run.status, run.completedAt])
}

async function selectRun(id) {
  await router.replace({ query: { ...route.query, run: id } })
}

async function refresh(background = false) {
  if (!props.visible || document.visibilityState !== 'visible' || (background && loading.value)) return
  const current = ++token
  const id = props.automationId
  const requestedRun = props.runId
  loading.value = true
  if (selectedRun.value?.id !== requestedRun && requestedRun) selectedRun.value = null
  try {
    const page = await automations.fetchRuns(id, 1, { suppressErrorToast: true, preserveLoaded: automations.page > 0 })
    if (!alive || current !== token) return
    loaded.value = true
    const selectedId = requestedRun || page.items[0]?.id
    const summary = page.items.find((run) => run.id === selectedId)
    if (!selectedId) selectedRun.value = null
    else if (selectedId !== selectedRun.value?.id || activeRun.value
      || (summary && runVersion(summary) !== runVersion(selectedRun.value))) {
      if (selectedId !== selectedRun.value?.id) selectedRun.value = null
      const run = await automations.fetchRun(id, selectedId, { suppressErrorToast: true })
      if (!alive || current !== token) return
      selectedRun.value = run
    }
    loadFailed.value = false
  } catch {
    if (alive && current === token) loadFailed.value = true
  } finally {
    if (alive && current === token) loading.value = false
  }
}

async function loadMore() {
  const current = token
  loading.value = true
  try {
    await automations.fetchRuns(props.automationId, automations.page + 1)
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    if (alive && current === token) loading.value = false
  }
}

function refreshVisible() { void refresh(true) }
watch(() => [props.automationId, props.runId, props.visible], (values, previous) => {
  token += 1
  loading.value = false
  if (!previous || values[0] !== previous[0] || values[1] !== previous[1]) selectedRun.value = null
  if (!previous || values[0] !== previous[0]) loaded.value = false
  void refresh()
}, { immediate: true })
onMounted(() => {
  timer = window.setInterval(refreshVisible, 5000)
  document.addEventListener('visibilitychange', refreshVisible)
})
onBeforeUnmount(() => {
  alive = false
  token += 1
  window.clearInterval(timer)
  document.removeEventListener('visibilitychange', refreshVisible)
})
</script>

<style scoped>
/* Preserve generated text and wrap unbroken URLs or identifiers on small screens. */
.result-content {
  overflow-wrap: anywhere;
}
</style>
