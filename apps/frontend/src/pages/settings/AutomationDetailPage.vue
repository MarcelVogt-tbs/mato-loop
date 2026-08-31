<template>
  <section :aria-busy="automations.loadingDetail || automations.loadingRuns">
    <q-toolbar class="settings-detail-toolbar q-px-none q-mb-md">
      <q-btn
        flat
        round
        dense
        icon="arrow_back"
        aria-label="Zurück zu den Automationen"
        :to="{ name: 'settings-automations' }"
      />
      <q-avatar color="primary" text-color="white" icon="history" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="text-h6 text-weight-bold">{{ automations.detail?.name || 'Automation' }}</div>
        <div class="text-body2 app-muted">Definition, Ausführungskontext und Laufhistorie</div>
      </q-toolbar-title>
      <q-btn
        v-if="automations.detail && !automations.detail.archivedAt"
        unelevated
        no-caps
        no-wrap
        color="primary"
        icon="edit"
        label="Bearbeiten"
        :to="{ name: 'settings-automation-edit', params: { automationId } }"
      />
    </q-toolbar>

    <q-banner v-if="detailLoadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Automation oder der Lauf konnte nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <div v-if="automations.detail" class="q-gutter-md">
      <q-card flat class="app-surface">
        <q-card-section>
          <div class="row items-center q-gutter-sm">
            <div class="text-subtitle1 text-weight-bold">{{ automations.detail.name }}</div>
            <q-chip dense :color="stateColor(automations.detail)" :text-color="statusTextColor(stateColor(automations.detail))">{{ stateLabel(automations.detail) }}</q-chip>
            <q-chip dense outline>Revision {{ automations.detail.revision }}</q-chip>
          </div>
          <div class="text-body2 text-pre-wrap q-mt-md">{{ automations.detail.task }}</div>
          <div class="row q-col-gutter-md q-mt-sm">
            <div class="col-12 col-sm-6">
              <div class="text-caption app-muted">Ausgabe-Session</div>
              <div>{{ automations.detail.outputSessionId ? sessionTitle(automations.detail.outputSessionId) || 'Session nicht verfügbar' : 'Keine' }}<span v-if="automations.detail.outputSessionRequired"> · erforderlich</span></div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption app-muted">Nächster Lauf</div>
              <div>{{ automations.detail.archivedAt ? `Archiviert am ${formatDate(automations.detail.archivedAt)}` : automations.detail.enabled ? formatDate(automations.detail.effectiveState?.nextRunAt) || 'Nicht geplant' : 'Pausiert' }}</div>
            </div>
          </div>
        </q-card-section>
      </q-card>

      <q-card flat class="app-surface">
        <q-expansion-item icon="schedule" label="Zeitpläne" header-class="text-weight-medium" default-opened>
          <q-separator />
          <q-list separator>
            <q-item v-for="schedule in automations.detail.schedules" :key="schedule.id || schedule.runAt || `${schedule.cron}-${schedule.timezone}`">
              <q-item-section>
                <q-item-label v-if="schedule.runAt">Einmalig am {{ formatDate(schedule.runAt) }}</q-item-label>
                <q-item-label v-else><code>{{ schedule.cron }}</code> · {{ schedule.timezone }}</q-item-label>
                <q-item-label v-if="schedule.validUntil" caption>Gültig bis einschließlich {{ formatDate(schedule.validUntil) }}</q-item-label>
                <q-item-label caption>{{ schedule.runAt ? 'Ausführung' : 'Nächster Lauf' }}: {{ scheduleNextRunLabel(automations.detail, schedule) }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <q-chip dense :color="automations.detail.enabled && schedule.enabled && !automations.detail.archivedAt ? 'positive' : 'grey-6'" :text-color="statusTextColor(automations.detail.enabled && schedule.enabled && !automations.detail.archivedAt ? 'positive' : 'grey-6')">{{ scheduleStateLabel(automations.detail, schedule) }}</q-chip>
              </q-item-section>
            </q-item>
          </q-list>
        </q-expansion-item>
      </q-card>

      <q-card flat class="app-surface">
        <q-expansion-item icon="tune" label="Laufzeitkontext" header-class="text-weight-medium">
          <q-separator />
          <q-list separator>
            <q-item v-for="item in runtimeItems" :key="item.label">
              <q-item-section>
                <q-item-label caption>{{ item.label }}</q-item-label>
                <q-item-label>{{ item.value }}</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
        </q-expansion-item>
      </q-card>

      <q-card flat class="app-surface">
        <q-expansion-item icon="forum" :label="`Ausführungskontext (${automations.detail.executionHistory?.length || 0})`" header-class="text-weight-medium">
          <q-separator />
          <q-list v-if="automations.detail.executionHistory?.length" separator>
            <q-item v-for="message in automations.detail.executionHistory" :key="message.id">
              <q-item-section avatar top><q-icon :name="message.role === 'user' ? 'person' : 'smart_toy'" /></q-item-section>
              <q-item-section>
                <q-item-label caption>{{ message.role === 'user' ? 'Auftrag' : 'Agent' }} · {{ formatDate(message.createdAt) }}</q-item-label>
                <q-item-label class="text-pre-wrap">{{ message.content }}</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
          <div v-else class="text-body2 app-muted q-pa-md">Noch kein Ausführungskontext vorhanden.</div>
        </q-expansion-item>
      </q-card>

      <q-card flat class="app-surface">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Laufhistorie</div>
          <div class="text-caption app-muted">Ausführungen, Ergebnisse und Zustellungsstatus.</div>
        </q-card-section>
        <q-separator />
        <q-card-section v-if="automations.selectedRun">
          <q-card flat class="app-surface-tonal">
            <q-card-section>
              <div class="row items-center q-gutter-sm">
                <q-chip dense :color="runStatus(automations.selectedRun).color" :text-color="statusTextColor(runStatus(automations.selectedRun).color)">{{ runStatus(automations.selectedRun).label }}</q-chip>
                <span class="text-caption app-muted">{{ triggerLabel(automations.selectedRun.trigger) }} · {{ formatDate(automations.selectedRun.createdAt) }}</span>
              </div>
              <div v-if="automations.selectedRun.scheduledFor" class="text-body2 q-mt-sm">Geplant für {{ formatDate(automations.selectedRun.scheduledFor) }}</div>
              <div v-if="automations.selectedRun.definition" class="q-mt-md">
                <div class="text-caption app-muted">Gespeicherte Definition</div>
                <div class="text-body2 text-pre-wrap">{{ automations.selectedRun.definition.task }}</div>
                <div v-if="automations.selectedRun.definition.skill?.name" class="text-body2 q-mt-xs">
                  Skill: {{ automations.selectedRun.definition.skill.name }}
                </div>
              </div>
              <div v-if="automations.selectedRun.scheduleSnapshots?.length" class="q-mt-md">
                <div class="text-caption app-muted">Zeitplan-Snapshots</div>
                <div v-for="(snapshot, index) in automations.selectedRun.scheduleSnapshots" :key="index" class="text-body2">
                  <template v-if="snapshot.runAt">Einmalig</template><template v-else><code>{{ snapshot.cron }}</code> · {{ snapshot.timezone }}</template><span v-if="snapshot.scheduledFor"> · {{ formatDate(snapshot.scheduledFor) }}</span><span v-if="snapshot.validUntil"> · gültig bis {{ formatDate(snapshot.validUntil) }}</span>
                </div>
              </div>
              <div v-if="automations.selectedRun.result?.content" class="q-mt-md">
                <div class="text-caption app-muted">Ergebnis</div>
                <div class="text-body2 text-pre-wrap">{{ automations.selectedRun.result.content }}</div>
              </div>
              <q-banner v-if="automations.selectedRun.error" rounded class="bg-negative text-white q-mt-md">
                {{ runError(automations.selectedRun) }}
              </q-banner>
              <div class="q-mt-md">
                <div class="text-caption app-muted">Zustellung</div>
                <div class="row items-center q-gutter-sm">
                  <q-chip dense :color="deliveryStatus(automations.selectedRun).color" :text-color="statusTextColor(deliveryStatus(automations.selectedRun).color)">{{ deliveryStatus(automations.selectedRun).label }}</q-chip>
                  <span class="text-body2">{{ automations.selectedRun.delivery?.attempts || 0 }} Versuch(e)</span>
                  <span v-if="automations.selectedRun.delivery?.nextAttemptAt" class="text-body2">Nächster Versuch: {{ formatDate(automations.selectedRun.delivery.nextAttemptAt) }}</span>
                </div>
                <q-btn
                  v-if="automations.selectedRun.delivery?.status === 'failed'"
                  outline
                  no-caps
                  color="primary"
                  icon="refresh"
                  label="Zustellung erneut versuchen"
                  class="q-mt-sm"
                  :loading="retryingRunId === automations.selectedRun.id"
                  @click="retryDelivery(automations.selectedRun)"
                />
              </div>
              <div class="row q-col-gutter-md q-mt-md text-caption app-muted">
                <div class="col-12 col-sm-4">Erstellt: {{ formatDate(automations.selectedRun.createdAt) }}</div>
                <div class="col-12 col-sm-4">Gestartet: {{ formatDate(automations.selectedRun.startedAt) || 'Noch nicht' }}</div>
                <div class="col-12 col-sm-4">Beendet: {{ formatDate(automations.selectedRun.completedAt) || 'Noch nicht' }}</div>
              </div>
            </q-card-section>
          </q-card>
        </q-card-section>
        <q-separator v-if="automations.selectedRun" />
        <q-list v-if="automations.runs.length" separator>
          <q-item
            v-for="run in automations.runs"
            :key="run.id"
            clickable
            v-ripple
            :active="automations.selectedRun?.id === run.id"
            :aria-current="automations.selectedRun?.id === run.id ? 'true' : undefined"
            @click="selectRun(run)"
          >
            <q-item-section avatar><q-icon :name="runStatus(run).icon" :color="runStatus(run).color" /></q-item-section>
            <q-item-section>
              <q-item-label>{{ runStatus(run).label }} · {{ triggerLabel(run.trigger) }}</q-item-label>
              <q-item-label caption>{{ formatDate(run.createdAt) }} · Revision {{ run.automationRevision }}</q-item-label>
            </q-item-section>
            <q-item-section side><q-icon name="chevron_right" /></q-item-section>
          </q-item>
        </q-list>
        <div v-else-if="!automations.loadingRuns" class="text-body2 app-muted text-center q-pa-lg">Noch keine Läufe.</div>
        <div class="row justify-center q-pa-md">
          <q-spinner v-if="automations.loadingRuns" color="primary" size="28px" />
          <q-btn v-else-if="automations.hasMore" outline no-caps color="primary" label="Weitere Läufe laden" @click="loadMoreRuns" />
        </div>
      </q-card>
    </div>

    <q-inner-loading :showing="automations.loadingDetail && !automations.detail" />
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage } from 'src/utils/api-errors'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { useAutomationStore } from 'stores/automations'
import { useChatStore } from 'stores/chat'
import { useExecutionAgentStore } from 'stores/execution-agents'

defineOptions({ name: 'AutomationDetailPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const automations = useAutomationStore()
const chat = useChatStore()
const executionAgents = useExecutionAgentStore()
const detailLoadFailed = ref(false)
const retryingRunId = ref(null)
let loadToken = 0
let refreshTimer = null
let refreshPending = false

const ACTIVE_STATUSES = new Set(['queued', 'running', 'waiting_approval', 'waiting_input'])
const automationId = computed(() => String(route.params.automationId || ''))
const runId = computed(() => {
  const value = route.query.run
  return String(Array.isArray(value) ? value[0] || '' : value || '')
})
const runtimeItems = computed(() => {
  const runtime = automations.detail?.runtime || {}
  const computer = executionAgents.byId(runtime.preferredExecutionAgentId)
  const labels = {
    full: 'Vollständig',
    read_only: 'Nur lesen',
    disabled: 'Deaktiviert',
    required: 'Erforderlich',
    available: 'Verfügbar'
  }
  return [
    ['Modell', runtime.modelId],
    ['Memory', runtime.memoryMode],
    ['Websuche', runtime.webSearchPolicy],
    ['Computer', runtime.preferredExecutionAgentId ? computer?.alias || 'Nicht verfügbar' : null],
    ['Aktueller Ordner', runtime.currentDirectory]
  ].map(([label, value]) => ({ label, value: labels[value] || value || 'Nicht festgelegt' }))
})

async function load() {
  const token = ++loadToken
  const id = automationId.value
  const selectedRunId = runId.value
  detailLoadFailed.value = false
  automations.clearDetail()
  try {
    await Promise.all([
      automations.fetchAutomation(id, { suppressErrorToast: true }),
      automations.fetchRuns(id, 1, { suppressErrorToast: true }),
      ...(selectedRunId ? [automations.fetchRun(id, selectedRunId, { suppressErrorToast: true })] : [])
    ])
  } catch {
    if (token === loadToken) detailLoadFailed.value = true
  }
}

async function selectRun(run) {
  await router.replace({
    name: 'settings-automation-detail',
    params: { automationId: run.automationId },
    query: { run: run.id }
  })
}

async function loadMoreRuns() {
  if (automations.detail) await automations.fetchRuns(automationId.value, automations.page + 1)
}

async function retryDelivery(run) {
  retryingRunId.value = run.id
  try {
    await automations.retryDelivery(run.automationId, run.id)
    $q.notify({ type: 'positive', message: 'Zustellung wurde erneut eingeplant' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    retryingRunId.value = null
  }
}

function activeStatus(automation) {
  const status = automation.effectiveState?.activeRunStatus
  return ACTIVE_STATUSES.has(status) ? status : null
}

function stateLabel(automation) {
  if (automation.archivedAt) return 'Archiviert'
  return activeStatus(automation)
    ? runStatus({ status: activeStatus(automation) }).label
    : automation.effectiveState?.enabled ? 'Aktiv' : automation.enabled ? 'Nicht geplant' : 'Pausiert'
}

function stateColor(automation) {
  return activeStatus(automation) ? runStatus({ status: activeStatus(automation) }).color : automation.effectiveState?.enabled ? 'positive' : 'grey-6'
}

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
  })[run?.status] || { label: run?.status || 'Unbekannt', color: 'grey-7', icon: 'help_outline' }
}

function deliveryStatus(run) {
  return ({
    not_requested: { label: 'Nicht angefordert', color: 'grey-7' },
    pending: { label: 'Ausstehend', color: 'primary' },
    retrying: { label: 'Erneuter Versuch', color: 'warning' },
    delivered: { label: 'Zugestellt', color: 'positive' },
    failed: { label: 'Fehlgeschlagen', color: 'negative' },
    cancelled: { label: 'Abgebrochen', color: 'grey-7' }
  })[run?.delivery?.status] || { label: run?.delivery?.status || 'Unbekannt', color: 'grey-7' }
}

function triggerLabel(trigger) {
  return ({ manual: 'Manuell', schedule: 'Zeitplan', scheduled: 'Zeitplan' })[trigger] || trigger || 'Unbekannt'
}

function scheduleExpired(schedule) {
  return schedule.validUntil && new Date(schedule.nextRunAt) > new Date(schedule.validUntil)
}

function scheduleStateLabel(automation, schedule) {
  if (automation.archivedAt) return 'Archiviert'
  if (!automation.enabled) return 'Pausiert'
  if (scheduleExpired(schedule)) return 'Abgelaufen'
  return schedule.enabled ? 'Aktiv' : 'Inaktiv'
}

function scheduleNextRunLabel(automation, schedule) {
  return schedule.enabled && automation.enabled && !automation.archivedAt
    ? formatDate(schedule.nextRunAt) || 'Nicht geplant'
    : scheduleStateLabel(automation, schedule)
}

function statusTextColor(color) {
  return ['positive', 'warning'].includes(color) ? 'dark' : 'white'
}

function sessionTitle(id) {
  return chat.sessionCatalog.find((session) => session.id === id)?.title
}

function formatDate(value) {
  return formatAbsoluteDateTime(value)
}

function runError(run) {
  return apiErrorMessage(run.error, 'Der Automationslauf ist fehlgeschlagen.')
}

async function refreshAutomationState() {
  if (refreshPending || !automations.detail) return
  refreshPending = true
  try {
    await Promise.all([
      automations.fetchAutomation(automationId.value, { suppressErrorToast: true }),
      automations.fetchRuns(automationId.value, 1, { suppressErrorToast: true, preserveLoaded: true }),
      ...(runId.value
        ? [automations.fetchRun(automationId.value, runId.value, { suppressErrorToast: true })]
        : [])
    ])
    detailLoadFailed.value = false
  } catch {
    // Background refresh keeps the last canonical state and retries later.
  } finally {
    refreshPending = false
  }
}

watch([automationId, runId], load, { immediate: true })

onMounted(() => {
  void executionAgents.fetchAll().catch(() => {})
  void chat.fetchSessionCatalog({ suppressErrorToast: true }).catch(() => {})
  refreshTimer = window.setInterval(refreshAutomationState, 5000)
})

onBeforeUnmount(() => {
  loadToken += 1
  window.clearInterval(refreshTimer)
  automations.clearDetail()
})
</script>
