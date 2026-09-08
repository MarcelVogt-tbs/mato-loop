<template>
  <router-view v-if="showSubpage" />
  <section v-else>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Automationen</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Agent-Aufträge mit einmaligen oder wiederkehrenden Zeitplänen und eigener Laufhistorie verwalten.
        </p>
      </div>
      <div class="row items-center q-gutter-sm wrap">
        <q-toggle v-model="showArchived" color="primary" label="Archiv anzeigen" />
        <q-btn
          v-if="!showArchived"
          unelevated
          no-caps
          color="primary"
          icon="add"
          label="Automation erstellen"
          :to="{ name: 'settings-automation-create' }"
        />
      </div>
    </header>

    <q-banner v-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Automationen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="loadAutomations" /></template>
    </q-banner>

    <q-card v-if="automations.loading && !loaded" flat class="app-surface text-center q-pa-xl">
      <q-spinner color="primary" size="36px" />
      <div class="app-muted q-mt-sm">Automationen werden geladen ...</div>
    </q-card>
    <q-card v-else-if="loaded && !automations.items.length" flat class="app-surface q-pa-xl">
      <AppStateMessage
        icon="schedule"
        :title="showArchived ? 'Keine archivierten Automationen' : 'Noch keine Automationen'"
        :description="!showArchived ? 'Erstelle einen Auftrag mit mindestens einem Zeitplan.' : ''"
      />
    </q-card>
    <div v-else class="row q-col-gutter-md">
      <div v-for="automation in automations.items" :key="automation.id" class="col-12">
        <q-card flat class="app-surface full-height column">
          <q-card-section class="row items-start no-wrap q-gutter-md">
            <q-avatar :color="automation.effectiveState?.enabled ? 'primary' : 'grey-6'" text-color="white" icon="schedule" />
            <div class="col">
              <div class="row items-center q-gutter-sm">
                <div class="text-subtitle1 text-weight-bold">{{ automation.name }}</div>
                <q-chip dense :color="stateColor(automation)" :text-color="statusTextColor(stateColor(automation))">{{ stateLabel(automation) }}</q-chip>
              </div>
              <div class="text-body2 app-muted q-mt-xs text-pre-wrap ellipsis-3-lines">{{ automation.task }}</div>
            </div>
          </q-card-section>
          <q-card-section class="q-pt-none row q-col-gutter-sm">
            <div class="col-12 col-sm-6">
              <div class="text-caption app-muted">Zeitpläne</div>
              <div class="text-body2">{{ automation.schedules.length }} · {{ activeScheduleCount(automation) }} aktiv</div>
            </div>
            <div class="col-12 col-sm-6">
              <div class="text-caption app-muted">Nächster Lauf</div>
              <div class="text-body2">{{ automation.archivedAt ? `Archiviert am ${formatDate(automation.archivedAt)}` : automation.enabled ? formatDate(automation.effectiveState?.nextRunAt) || 'Nicht geplant' : 'Pausiert' }}</div>
            </div>
          </q-card-section>
          <q-space />
          <q-separator />
          <q-card-actions align="right" class="q-pa-sm">
            <q-btn
              v-if="!automation.archivedAt"
              flat
              no-caps
              icon="play_arrow"
              label="Jetzt ausführen"
              :loading="runningId === automation.id"
              :disable="Boolean(activeStatus(automation)) || Boolean(runningId)"
              @click="runNow(automation)"
            >
              <q-tooltip v-if="activeStatus(automation)">Es läuft bereits ein Auftrag.</q-tooltip>
            </q-btn>
            <q-btn
              flat
              no-caps
              icon="history"
              label="Details/Verlauf"
              :to="{ name: 'settings-automation-detail', params: { automationId: automation.id } }"
            />
            <q-btn flat round icon="more_vert" :aria-label="`Aktionen für ${automation.name}`">
              <q-menu class="app-action-menu">
                <q-list padding>
                  <q-item
                    v-if="!automation.archivedAt"
                    clickable
                    v-ripple
                    v-close-popup
                    :to="{ name: 'settings-automation-edit', params: { automationId: automation.id } }"
                  >
                    <q-item-section avatar><q-icon name="edit" /></q-item-section>
                    <q-item-section>Bearbeiten</q-item-section>
                  </q-item>
                  <q-item clickable v-ripple v-close-popup class="text-negative" @click="removeAutomation(automation)">
                    <q-item-section avatar><q-icon name="delete_outline" /></q-item-section>
                    <q-item-section>Löschen</q-item-section>
                  </q-item>
                </q-list>
              </q-menu>
            </q-btn>
          </q-card-actions>
        </q-card>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import AppStateMessage from 'components/AppStateMessage.vue'
import { useConfirm } from 'src/composables/confirm'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { useAutomationStore } from 'stores/automations'

defineOptions({ name: 'AutomationSettingsPage' })

const ACTIVE_STATUSES = new Set(['queued', 'running', 'waiting_approval', 'waiting_input'])
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const automations = useAutomationStore()
const loaded = ref(false)
const loadFailed = ref(false)
const showArchived = ref(false)
const runningId = ref(null)
let refreshTimer = null
let refreshPending = false

const legacyAutomationId = computed(() => route.name === 'settings-automations' ? route.query.automation : null)
const listVisible = computed(() => route.name === 'settings-automations' && !legacyAutomationId.value)
const showSubpage = computed(() => !listVisible.value)

async function loadAutomations() {
  loadFailed.value = false
  try {
    await automations.fetchAutomations({ archived: showArchived.value })
    loaded.value = true
  } catch {
    loadFailed.value = true
  }
}

async function removeAutomation(automation) {
  const accepted = await confirm(
    `Soll die Automation „${automation.name}“ samt Laufhistorie dauerhaft gelöscht werden?`,
    'Automation löschen?',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  try {
    await automations.deleteAutomation(automation.id, automation.revision)
    $q.notify({ type: 'positive', message: 'Automation gelöscht' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function runNow(automation) {
  runningId.value = automation.id
  try {
    const run = await automations.runNow(automation.id, automation.revision)
    $q.notify({ type: 'positive', message: 'Automation wurde gestartet' })
    await router.push({
      name: 'settings-automation-detail',
      params: { automationId: automation.id },
      query: { run: run.id }
    })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    runningId.value = null
  }
}

function activeStatus(automation) {
  const status = automation.effectiveState?.activeRunStatus
  return ACTIVE_STATUSES.has(status) ? status : null
}

function stateLabel(automation) {
  if (automation.archivedAt) return 'Archiviert'
  return activeStatus(automation)
    ? runStatus(activeStatus(automation)).label
    : automation.effectiveState?.enabled ? 'Aktiv' : automation.enabled ? 'Nicht geplant' : 'Pausiert'
}

function stateColor(automation) {
  return activeStatus(automation) ? runStatus(activeStatus(automation)).color : automation.effectiveState?.enabled ? 'positive' : 'grey-6'
}

function runStatus(status) {
  return ({
    queued: { label: 'Eingereiht', color: 'primary' },
    running: { label: 'Läuft', color: 'primary' },
    waiting_approval: { label: 'Wartet auf Freigabe', color: 'warning' },
    waiting_input: { label: 'Wartet auf Eingabe', color: 'warning' }
  })[status] || { label: status || 'Unbekannt', color: 'grey-7' }
}

function activeScheduleCount(automation) {
  return automation.enabled ? automation.schedules.filter((schedule) => schedule.enabled).length : 0
}

function statusTextColor(color) {
  return ['positive', 'warning'].includes(color) ? 'dark' : 'white'
}

function formatDate(value) {
  return formatAbsoluteDateTime(value)
}

async function refreshAutomationState() {
  if (refreshPending || !listVisible.value) return
  refreshPending = true
  try {
    await automations.fetchAutomations({ archived: showArchived.value, suppressErrorToast: true })
    loaded.value = true
    loadFailed.value = false
  } catch {
    // Background refresh keeps the last canonical state and retries later.
  } finally {
    refreshPending = false
  }
}

function stopRefresh() {
  window.clearInterval(refreshTimer)
  refreshTimer = null
}

function activateList() {
  void loadAutomations()
  if (!refreshTimer) refreshTimer = window.setInterval(refreshAutomationState, 5000)
}

function redirectLegacyDetail() {
  const automationId = Array.isArray(legacyAutomationId.value) ? legacyAutomationId.value[0] : legacyAutomationId.value
  if (!automationId) return false
  const runValue = Array.isArray(route.query.run) ? route.query.run[0] : route.query.run
  stopRefresh()
  void router.replace({
    name: 'settings-automation-detail',
    params: { automationId },
    ...(runValue ? { query: { run: runValue } } : {})
  })
  return true
}

watch(showArchived, () => {
  if (!listVisible.value) return
  loaded.value = false
  void loadAutomations()
})

watch(listVisible, (visible) => {
  if (redirectLegacyDetail()) return
  if (visible) activateList()
  else stopRefresh()
})

onMounted(() => {
  if (!redirectLegacyDetail() && listVisible.value) activateList()
})
onBeforeUnmount(stopRefresh)
</script>
