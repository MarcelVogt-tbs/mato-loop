<template>
  <section :aria-busy="loading">
    <q-toolbar class="q-px-none">
      <q-btn flat round dense icon="arrow_back" aria-label="Zurück zu den Automationen" :to="{ name: 'settings-automations' }" />
      <q-toolbar-title>
        <h1 class="text-h6 q-my-none ellipsis">{{ isCreate ? 'Neue Automation' : editingAutomation?.name || 'Automation' }}</h1>
      </q-toolbar-title>
    </q-toolbar>
    <div class="row items-center justify-between q-gutter-sm q-mb-md">
      <div class="text-body2">
        <template v-if="savedState">
          <q-icon :name="status.icon" :color="status.color" class="q-mr-xs" />{{ status.label }}
          <div class="text-caption app-muted">{{ automationTiming(savedState) }}</div>
        </template>
        <span v-else-if="isCreate" class="app-muted">Noch nicht gespeichert</span>
        <div v-if="dirty" class="text-caption app-muted">Ungespeicherte Änderungen</div>
      </div>
      <div class="row items-center q-gutter-sm">
        <q-btn v-if="!isCreate" flat no-caps no-wrap icon="play_arrow" label="Jetzt ausführen" :loading="running" :disable="!canRun" @click="runNow" />
        <q-btn unelevated no-caps no-wrap color="primary" icon="save" label="Speichern" aria-label="Automation speichern" :loading="saving" :disable="loading || loadFailed || blocked || conflict || !dirty" @click="saveAutomation" />
      </div>
    </div>
    <div v-if="!isCreate && dirty" class="text-caption app-muted q-mb-sm">Vor „Jetzt ausführen“ bitte Änderungen speichern. Ausgeführt wird nur der gespeicherte Auftrag.</div>

    <q-banner v-if="blocked" class="app-surface-tonal q-mb-md">Archivierte Automationen sind schreibgeschützt. Der Verlauf bleibt verfügbar.</q-banner>
    <q-banner v-if="conflict" class="app-surface-tonal q-mb-md">
      Diese Automation wurde zwischenzeitlich geändert. Deine Eingaben bleiben erhalten. Bitte lade den aktuellen Stand vor dem Speichern oder Ausführen.
      <template #action><q-btn flat no-caps color="primary" label="Aktuellen Stand laden" @click="reloadDefinition" /></template>
    </q-banner>
    <q-banner v-if="loadFailed" class="bg-negative text-white q-mb-md">
      {{ notFound ? 'Die Automation wurde nicht gefunden.' : 'Die Automation konnte nicht geladen werden.' }}
      <template #action><q-btn v-if="!notFound" flat no-caps label="Erneut laden" @click="reloadDefinition" /></template>
    </q-banner>
    <q-banner v-if="sessionLoadFailed" class="bg-negative text-white q-mb-md">
      Die Sessions konnten nicht vollständig geladen werden.
      <template #action><q-btn flat no-caps label="Erneut laden" :loading="sessionsLoading" @click="loadSessions" /></template>
    </q-banner>

    <q-tabs v-model="tab" dense no-caps align="left" active-color="primary" indicator-color="primary">
      <q-tab id="automation-settings-tab" name="settings" label="Einstellungen" aria-controls="automation-settings-panel" />
      <q-tab id="automation-history-tab" name="history" label="Verlauf" aria-controls="automation-history-panel" />
    </q-tabs>
    <q-separator />
    <!-- Keep the form mounted: tab and notification navigation must not reset drafts. -->
    <div v-show="tab === 'settings'" id="automation-settings-panel" role="tabpanel" aria-labelledby="automation-settings-tab">
      <q-form v-if="!loadFailed && !loading" ref="formRef" :inert="saving ? '' : null" @submit.prevent="saveAutomation">
        <section class="q-py-md" aria-labelledby="automation-task-heading">
          <h2 id="automation-task-heading" class="text-subtitle1 text-weight-medium q-mt-none q-mb-md">Auftrag</h2>
          <div class="q-gutter-md">
            <q-input v-model="form.name" :readonly="blocked" outlined dense label="Name" maxlength="240" hide-bottom-space lazy-rules :rules="[requiredRule]" />
            <q-input v-model="form.task" :readonly="blocked" outlined type="textarea" autogrow label="Aufgabe" placeholder="Was soll die Automation erledigen und welches Ergebnis erwartest du?" maxlength="40000" hide-bottom-space lazy-rules :rules="[requiredRule]" />
          </div>
          <p class="text-caption app-muted q-mt-sm q-mb-none">Frühere Ergebnisse und Dateien können in spätere Läufe einfließen.</p>
          <q-expansion-item v-model="taskAdvanced" dense label="Weitere Auftragseinstellungen" :caption="taskOptionsSummary" class="q-mt-md">
            <div class="q-pa-sm q-gutter-md">
              <q-select v-model="form.skillId" :readonly="blocked" outlined dense emit-value map-options clearable label="Skill verbindlich verwenden (optional)" :options="skillOptions" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" />
              <p class="text-caption app-muted">Eine Bindung gibt bestimmte Anweisungen vor, keine neuen Werkzeuge. Ohne Bindung kann das Modell Skills automatisch auswählen.</p>
              <q-toggle v-if="!isCreate" v-model="form.copyRuntime" :disable="blocked" dense label="Einstellungen einmalig aus einer Session übernehmen" />
              <template v-if="isCreate || form.copyRuntime">
                <q-select v-model="form.sourceSessionId" :readonly="blocked" outlined dense emit-value map-options clearable label="Einstellungen aus Session übernehmen" :options="sessionOptions" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" :rules="!isCreate && form.copyRuntime ? [requiredRule] : []" hide-bottom-space />
                <p class="text-caption app-muted">Einmalige Kopie beim Speichern: Modell, Memory, Websuche, Computer und Ordner. Keine laufende Synchronisierung.</p>
              </template>
              <div class="text-subtitle2">{{ isCreate ? 'Ohne Übernahme gelten die aktuellen Standardeinstellungen.' : 'Aktuell gespeicherte Ausführungseinstellungen' }}</div>
              <q-list v-if="!isCreate" dense>
                <q-item v-for="item in runtimeItems" :key="item.label" class="q-px-none">
                  <q-item-section><q-item-label caption>{{ item.label }}</q-item-label><q-item-label class="runtime-value">{{ item.value }}</q-item-label></q-item-section>
                </q-item>
              </q-list>
              <q-expansion-item v-if="editingAutomation?.runtime?.modelId" dense label="Technische Modellkennung">
                <div class="q-pa-sm text-caption runtime-value">{{ editingAutomation.runtime.modelId }}</div>
              </q-expansion-item>
            </div>
          </q-expansion-item>
        </section>
        <q-separator />
        <section class="q-py-md" aria-labelledby="automation-execution-heading">
          <h2 id="automation-execution-heading" class="text-subtitle1 text-weight-medium q-mt-none q-mb-md">Ausführung</h2>
          <q-select :model-value="executionMode" :readonly="blocked" outlined dense emit-value map-options label="Starten" :options="executionOptions" @update:model-value="setExecutionMode" />
          <div v-if="form.manual" class="text-caption app-muted q-mt-sm">Nur über „Jetzt ausführen“. Es werden keine Zeitpläne gespeichert.<span v-if="form.schedules.length"> Deine Zeitplanentwürfe bleiben bis zum Verlassen oder Speichern erhalten.</span></div>
          <template v-else>
            <q-toggle v-model="form.enabled" :disable="blocked" dense label="Zeitpläne aktiv" class="q-mt-md" />
            <div class="text-caption app-muted q-mt-xs">{{ form.enabled ? 'Geplante Starts sind eingeschaltet.' : 'Zeitpläne pausiert.' }} Manuelle Läufe bleiben möglich.</div>
            <AutomationScheduleEditor v-for="(schedule, index) in form.schedules" :key="index" v-model="form.schedules[index]" :readonly="blocked" :index="index" :multiple="form.schedules.length > 1" :removable="form.schedules.length > 1" :error="validationAttempted ? scheduleError(schedule) : ''" @remove="removeSchedule(index)" />
          </template>
          <q-expansion-item v-if="!form.manual" v-model="executionAdvanced" dense label="Weitere Ausführungseinstellungen" :caption="executionOptionsSummary" class="q-mt-sm">
            <div class="q-pa-sm q-gutter-sm">
              <q-btn flat no-caps icon="add" color="primary" label="Weiteren Zeitplan hinzufügen" :disable="blocked || form.schedules.length >= 50" @click="form.schedules.push(scheduleDraft())" />
              <q-toggle v-if="canArchiveAfterSuccess" v-model="form.archiveAfterSuccess" :disable="blocked" dense label="Nach erfolgreichem geplanten Termin archivieren" />
            </div>
          </q-expansion-item>
        </section>
        <q-separator />
        <section class="q-py-md" aria-labelledby="automation-result-heading">
          <h2 id="automation-result-heading" class="text-subtitle1 text-weight-medium q-mt-none q-mb-sm">Ergebnis</h2>
          <p class="text-body2">Ergebnisse und Fehler findest du immer im Verlauf dieser Automation.</p>
          <q-select v-model="form.outputSessionId" :readonly="blocked" outlined dense emit-value map-options clearable label="Zusätzlich in Session ausgeben (optional)" :options="sessionOptions" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" @update:model-value="clearOutputRequirement" />
          <q-expansion-item v-if="form.outputSessionId" dense label="Weitere Ergebniseinstellungen" :caption="outputOptionsSummary" class="q-mt-sm">
            <div class="q-pa-sm q-gutter-md">
              <q-toggle v-model="form.outputSessionRequired" :disable="blocked" dense label="Automation an diesen Chat binden" />
              <div class="text-caption app-muted">Beim Löschen dieses Chats muss auch die Automation gelöscht werden.</div>
              <q-select v-model="form.deliveryTarget" :readonly="blocked" outlined dense emit-value map-options label="Zustellkanal" :options="deliveryOptions" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" />
              <p class="text-caption app-muted">Sessionstandard übernimmt den Kanal der ersten Nachricht in der Ausgabe-Session.</p>
            </div>
          </q-expansion-item>
        </section>
      </q-form>
    </div>
    <div v-show="tab === 'history'" id="automation-history-panel" role="tabpanel" aria-labelledby="automation-history-tab">
      <AutomationRunHistory v-if="editingAutomation" :key="editingAutomation.id" :automation-id="editingAutomation.id" :visible="tab === 'history'" :run-id="runId" />
      <p v-else-if="isCreate && !loading && !loadFailed" class="text-body2 app-muted q-py-md">Nach dem Speichern erscheinen hier die Läufe und Ergebnisse.</p>
    </div>
    <q-inner-loading :showing="loading" />
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import AutomationScheduleEditor from 'src/components/AutomationScheduleEditor.vue'
import AutomationRunHistory from 'src/components/AutomationRunHistory.vue'
import { useConfirm } from 'src/composables/confirm'
import { scheduleDraft, scheduleError, schedulePayload } from 'src/utils/automation-schedules'
import { automationStatus, automationTiming } from 'src/utils/automation-status'
import { useAutomationStore } from 'stores/automations'
import { useChannelStore } from 'stores/channels'
import { useChatStore } from 'stores/chat'
import { useSkillStore } from 'stores/skills'
import { useExecutionAgentStore } from 'stores/execution-agents'

defineOptions({ name: 'AutomationEditorPage' })
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const automations = useAutomationStore()
const channels = useChannelStore()
const chat = useChatStore()
const skills = useSkillStore()
const executionAgents = useExecutionAgentStore()
const formRef = ref(null)
const editingAutomation = ref(null)
const liveStatus = ref(null)
const loading = ref(false)
const saving = ref(false)
const running = ref(false)
const loadFailed = ref(false)
const notFound = ref(false)
const sessionLoadFailed = ref(false)
const sessionsLoading = ref(false)
const savedDraft = ref('')
const tab = ref('settings')
const taskAdvanced = ref(false)
const executionAdvanced = ref(false)
const validationAttempted = ref(false)
let loadToken = 0
let statusToken = 0
let refreshTimer
let refreshPending = false
let alive = true
let internalSaveId = null

const form = reactive({
  name: '', task: '', skillId: null, enabled: true, sourceSessionId: null, copyRuntime: false,
  outputSessionId: null, outputSessionRequired: false, deliveryTarget: 'default', archiveAfterSuccess: false,
  manual: true, schedules: []
})
const executionOptions = [{ label: 'Manuell', value: 'manual' }, { label: 'Einmalig', value: 'once' }, { label: 'Wiederkehrend', value: 'cron' }]
const automationId = computed(() => String(route.params.automationId || ''))
const isCreate = computed(() => route.name === 'settings-automation-create')
const runId = computed(() => String([route.query.run].flat()[0] || ''))
const savedState = computed(() => liveStatus.value || editingAutomation.value)
const status = computed(() => automationStatus(savedState.value))
const blocked = computed(() => Boolean(savedState.value?.archivedAt))
const conflict = computed(() => Boolean(liveStatus.value && editingAutomation.value && liveStatus.value.revision !== editingAutomation.value.revision))
const dirty = computed(() => Boolean(savedDraft.value) && draftSignature() !== savedDraft.value)
const canRun = computed(() => editingAutomation.value && !loading.value && !loadFailed.value && !saving.value && !running.value && !dirty.value && !blocked.value && !conflict.value && !savedState.value?.effectiveState?.activeRunStatus)
const executionMode = computed(() => form.manual ? 'manual' : form.schedules[0]?.kind || 'cron')
const canArchiveAfterSuccess = computed(() => !form.manual && form.schedules.length === 1 && form.schedules[0].kind === 'once')
const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
const skillOptions = computed(() => {
  const options = skills.items.map((skill) => ({ label: skill.name, value: skill.id }))
  if (form.skillId && !options.some((item) => item.value === form.skillId)) options.push({ label: 'Gespeicherter Skill nicht verfügbar', value: form.skillId })
  return options
})
const sessionOptions = computed(() => {
  const options = chat.sessionCatalog.map((session) => ({ label: session.title, value: session.id }))
  for (const id of [form.sourceSessionId, form.outputSessionId]) {
    if (id && !options.some((option) => option.value === id)) options.push({ label: 'Session nicht verfügbar', value: id })
  }
  return options
})
const deliveryOptions = computed(() => {
  const allowed = new Set(channels.providers.filter((provider) => provider.allowed).map((provider) => provider.provider))
  const endpoints = channels.endpoints.filter((endpoint) => allowed.has(endpoint.provider) && endpoint.enabled && endpoint.pairingState === 'paired')
    .map((endpoint) => ({ label: channelEndpointLabel(endpoint), value: endpoint.id }))
  if (!['default', 'app'].includes(form.deliveryTarget) && !endpoints.some((option) => option.value === form.deliveryTarget)) {
    const saved = channels.endpoints.find((endpoint) => endpoint.id === form.deliveryTarget)
    endpoints.unshift({ label: saved ? `${channelEndpointLabel(saved)} (nicht verfügbar)` : 'Gespeicherter Kanal nicht verfügbar', value: form.deliveryTarget, disable: true })
  }
  return [{ label: 'Sessionstandard', value: 'default' }, { label: 'MatoLoop-App', value: 'app' }, ...endpoints]
})
const taskOptionsSummary = computed(() => [
  form.skillId ? `Skill: ${skillOptions.value.find((item) => item.value === form.skillId)?.label}` : '',
  (isCreate.value || form.copyRuntime) && form.sourceSessionId ? 'Einmalige Übernahme aus Session vorgemerkt' : ''
].filter(Boolean).join(' · '))
const executionOptionsSummary = computed(() => [
  form.schedules.length > 1 ? `${form.schedules.length} Zeitpläne` : '',
  canArchiveAfterSuccess.value && form.archiveAfterSuccess ? 'Archivieren nach erfolgreichem geplanten Termin' : ''
].filter(Boolean).join(' · '))
const outputOptionsSummary = computed(() => [
  form.outputSessionRequired ? 'An diesen Chat gebunden' : '',
  deliveryOptions.value.find((item) => item.value === form.deliveryTarget)?.label
].filter(Boolean).join(' · '))
const runtimeItems = computed(() => {
  const runtime = editingAutomation.value?.runtime || {}
  const labels = { full: 'Vollständig', read_only: 'Nur lesen', disabled: 'Deaktiviert', required: 'Erforderlich', available: 'Verfügbar' }
  return [
    ['Modell', runtime.modelId ? 'Gespeichertes Modell' : 'Standardmodell'], ['Memory', runtime.memoryMode], ['Websuche', runtime.webSearchPolicy],
    ['Computer', runtime.preferredExecutionAgentId ? executionAgents.byId(runtime.preferredExecutionAgentId)?.alias || 'Nicht verfügbar' : null],
    ['Aktueller Ordner', runtime.currentDirectory]
  ].map(([label, value]) => ({ label, value: labels[value] || value || 'Nicht festgelegt' }))
})

function channelEndpointLabel(endpoint) {
  return `${endpoint.name} · ${channels.providers.find((item) => item.provider === endpoint.provider)?.label || endpoint.provider}`
}

function draftSignature() {
  return JSON.stringify(form, (key, value) => key === 'advanced' ? undefined : value)
}

function setExecutionMode(mode) {
  form.manual = mode === 'manual'
  if (form.manual) return
  if (!form.schedules.length) form.schedules.push(scheduleDraft())
  form.schedules[0].kind = mode
}

async function removeSchedule(index) {
  const token = loadToken
  const schedule = form.schedules[index]
  if (await confirm('Diesen Zeitplan aus dem Entwurf entfernen?', 'Zeitplan entfernen?', { okLabel: 'Entfernen' }) && alive && token === loadToken && form.schedules[index] === schedule) form.schedules.splice(index, 1)
}

function clearOutputRequirement(value) {
  if (!value) {
    form.outputSessionRequired = false
    form.deliveryTarget = 'default'
  }
}

function requestPayload() {
  return {
    name: form.name.trim(), task: form.task.trim(), skillId: form.skillId || null, enabled: form.enabled,
    sourceSessionId: editingAutomation.value && !form.copyRuntime ? null : form.sourceSessionId || null,
    outputSessionId: form.outputSessionId || null, outputSessionRequired: Boolean(form.outputSessionId && form.outputSessionRequired),
    deliveryChannelType: form.deliveryTarget === 'default' ? null : form.deliveryTarget === 'app' ? 'app' : 'endpoint',
    deliveryEndpointId: !['default', 'app'].includes(form.deliveryTarget) ? form.deliveryTarget : null,
    archiveAfterSuccess: Boolean(canArchiveAfterSuccess.value && form.archiveAfterSuccess),
    schedules: form.manual ? [] : form.schedules.map(schedulePayload)
  }
}

function assignForm(automation = null) {
  editingAutomation.value = automation ? structuredClone(automation) : null
  liveStatus.value = null
  statusToken += 1
  Object.assign(form, {
    name: automation?.name || '', task: automation?.task || '', skillId: automation?.skillId || null,
    enabled: automation?.enabled ?? true, sourceSessionId: null, copyRuntime: false,
    outputSessionId: automation?.outputSessionId || null, outputSessionRequired: automation?.outputSessionRequired || false,
    deliveryTarget: automation?.deliveryChannel?.type === 'endpoint' ? automation.deliveryChannel.endpointId : automation?.deliveryChannel?.type || 'default',
    archiveAfterSuccess: automation?.archiveAfterSuccess || false,
    manual: !automation?.schedules?.length, schedules: automation?.schedules?.map(scheduleDraft) || []
  })
  savedDraft.value = draftSignature()
  validationAttempted.value = false
}

async function loadSessions() {
  const token = loadToken
  sessionsLoading.value = true
  sessionLoadFailed.value = false
  try {
    await chat.fetchSessionCatalog({ suppressErrorToast: true })
  } catch {
    if (alive && token === loadToken) sessionLoadFailed.value = true
  } finally {
    if (alive && token === loadToken) sessionsLoading.value = false
  }
}

async function load() {
  const token = ++loadToken
  statusToken += 1
  loading.value = true
  loadFailed.value = false
  notFound.value = false
  editingAutomation.value = null
  liveStatus.value = null
  savedDraft.value = ''
  automations.clearDetail()
  tab.value = runId.value ? 'history' : 'settings'
  void loadSessions()
  try {
    const automation = isCreate.value ? null : await automations.fetchAutomation(automationId.value, { suppressErrorToast: true })
    if (alive && token === loadToken) assignForm(automation)
  } catch (error) {
    if (!alive || token !== loadToken) return
    notFound.value = error?.response?.status === 404
    loadFailed.value = true
  } finally {
    if (alive && token === loadToken) loading.value = false
  }
}

async function saveAutomation() {
  if (loading.value || saving.value || loadFailed.value || blocked.value || conflict.value || !dirty.value) return
  const token = loadToken
  saving.value = true
  tab.value = 'settings'
  validationAttempted.value = true
  const invalidSchedules = !form.manual && form.schedules.some((schedule) => {
    if (!scheduleError(schedule)) return false
    schedule.advanced = true
    return true
  })
  if (form.copyRuntime && !form.sourceSessionId) taskAdvanced.value = true
  await nextTick()
  const validForm = await formRef.value?.validate()
  if (!alive || token !== loadToken) return
  if (!validForm || invalidSchedules || (form.copyRuntime && !form.sourceSessionId)) {
    saving.value = false
    return
  }
  const creating = isCreate.value
  statusToken += 1
  try {
    const payload = requestPayload()
    const saved = editingAutomation.value
      ? await automations.updateAutomation(editingAutomation.value.id, { ...payload, expectedRevision: editingAutomation.value.revision })
      : await automations.createAutomation(payload)
    if (!alive || token !== loadToken) return
    assignForm(saved)
    if (creating) {
      internalSaveId = saved.id
      await router.replace({ name: 'settings-automation-detail', params: { automationId: saved.id } })
    }
    if (alive) $q.notify({ type: 'positive', message: creating ? 'Automation erstellt' : 'Automation gespeichert' })
  } catch {
    // The central Axios interceptor displays the error; keep the editing revision.
  } finally {
    internalSaveId = null
    if (alive && token === loadToken) saving.value = false
  }
}

async function runNow() {
  if (!canRun.value) return
  const token = loadToken
  statusToken += 1
  running.value = true
  try {
    const run = await automations.runNow(editingAutomation.value.id, editingAutomation.value.revision)
    if (!alive || token !== loadToken) return
    statusToken += 1
    liveStatus.value = { ...savedState.value, effectiveState: { ...savedState.value.effectiveState, activeRunStatus: run.status } }
    tab.value = 'history'
    await router.replace({ query: { ...route.query, run: run.id } })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    if (alive && token === loadToken) running.value = false
  }
}

async function refreshStatus() {
  if (document.visibilityState !== 'visible' || refreshPending || loading.value || saving.value || running.value || !editingAutomation.value) return
  const token = ++statusToken
  const pageToken = loadToken
  refreshPending = true
  try {
    const status = await automations.fetchAutomationStatus(editingAutomation.value.id, { suppressErrorToast: true })
    if (alive && pageToken === loadToken && token === statusToken) liveStatus.value = status
  } catch {
    // Keep the last saved status; the next visible poll retries.
  } finally {
    refreshPending = false
  }
}

async function confirmDiscardedChanges() {
  if (saving.value) return false
  return !dirty.value || await confirm('Sollen die ungespeicherten Änderungen verworfen werden?', 'Änderungen verwerfen?', { okLabel: 'Verwerfen' })
}

async function reloadDefinition() {
  const token = loadToken
  if (await confirmDiscardedChanges() && alive && token === loadToken) await load()
}

function preventDiscardOnUnload(event) {
  if (!dirty.value && !saving.value) return
  event.preventDefault()
  event.returnValue = ''
}

watch(automationId, () => {
  if (internalSaveId === automationId.value) return
  saving.value = false
  running.value = false
  void load()
})
watch(runId, (id) => { if (id) tab.value = 'history' })
onBeforeRouteUpdate((to, from) => {
  if (to.params.automationId === from.params.automationId || to.params.automationId === internalSaveId) return true
  return confirmDiscardedChanges()
})
onBeforeRouteLeave((to) => to.params.automationId === internalSaveId && internalSaveId ? true : confirmDiscardedChanges())
onMounted(() => {
  void load()
  void skills.fetchSkills().catch(() => {})
  void channels.fetchAll({ suppressErrorToast: true }).catch(() => {})
  void executionAgents.fetchAll({ suppressErrorToast: true }).catch(() => {})
  refreshTimer = window.setInterval(refreshStatus, 5000)
  document.addEventListener('visibilitychange', refreshStatus)
  window.addEventListener('beforeunload', preventDiscardOnUnload)
})
onBeforeUnmount(() => {
  alive = false
  loadToken += 1
  statusToken += 1
  window.clearInterval(refreshTimer)
  document.removeEventListener('visibilitychange', refreshStatus)
  window.removeEventListener('beforeunload', preventDiscardOnUnload)
  automations.clearDetail()
})
</script>

<style scoped>
/* Filesystem paths need not contain natural line-break opportunities. */
.runtime-value { overflow-wrap: anywhere; }
</style>
