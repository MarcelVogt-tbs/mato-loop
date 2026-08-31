<template>
  <section :aria-busy="loading">
    <q-toolbar class="settings-detail-toolbar q-px-none q-mb-md">
      <q-btn
        flat
        round
        dense
        icon="arrow_back"
        aria-label="Zurück zu den Automationen"
        :disable="saving"
        :to="{ name: 'settings-automations' }"
      />
      <q-avatar color="primary" text-color="white" :icon="isCreate ? 'add_task' : 'edit_calendar'" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="row items-center q-gutter-sm">
          <div class="text-h6 text-weight-bold">
            {{ isCreate ? 'Automation erstellen' : (editingAutomation?.name || 'Automation bearbeiten') }}
          </div>
          <q-badge v-if="dirty" color="warning" text-color="black" label="Ungespeichert" />
        </div>
        <div class="text-body2 app-muted">Auftrag, Laufzeit, Ausgabe und Zeitpläne festlegen.</div>
      </q-toolbar-title>
      <q-btn
        unelevated
        no-caps
        no-wrap
        color="primary"
        icon="save"
        label="Speichern"
        aria-label="Automation speichern"
        :loading="saving"
        :disable="loading || loadFailed || blocked || !dirty"
        @click="saveAutomation"
      />
    </q-toolbar>

    <q-banner v-if="blocked" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="archive" color="warning" /></template>
      Archivierte Automationen können nicht bearbeitet werden.
      <template #action>
        <q-btn
          flat
          no-caps
          color="primary"
          label="Details anzeigen"
          :to="{ name: 'settings-automation-detail', params: { automationId: editingAutomation.id } }"
        />
      </template>
    </q-banner>

    <q-banner v-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      {{ notFound ? 'Die Automation wurde nicht gefunden.' : 'Die Automation konnte nicht geladen werden.' }}
      <template #action>
        <q-btn v-if="!notFound" flat no-caps color="white" label="Erneut laden" @click="load" />
        <q-btn v-else flat no-caps color="white" label="Zurück" :to="{ name: 'settings-automations' }" />
      </template>
    </q-banner>

    <q-banner v-if="sessionLoadFailed && !loadFailed" rounded class="bg-negative text-white q-mb-md">
      Die Sessions konnten nicht vollständig geladen werden.
      <template #action>
        <q-btn flat no-caps color="white" label="Erneut laden" :loading="sessionsLoading" @click="loadSessions" />
      </template>
    </q-banner>

    <q-form
      v-if="!loadFailed"
      ref="formRef"
      :class="{ disabled: blocked }"
      :inert="blocked"
      @submit.prevent="saveAutomation"
    >
      <q-tabs
        v-model="section"
        dense
        no-caps
        align="left"
        active-color="primary"
        indicator-color="primary"
        mobile-arrows
        outside-arrows
      >
        <q-tab id="automation-task-tab" name="task" icon="assignment" label="Auftrag" aria-controls="automation-task-panel" />
        <q-tab id="automation-runtime-tab" name="runtime" icon="tune" label="Laufzeit" aria-controls="automation-runtime-panel" />
        <q-tab id="automation-schedules-tab" name="schedules" icon="schedule" label="Zeitpläne" aria-controls="automation-schedules-panel" />
      </q-tabs>
      <q-separator class="q-mb-lg" />

      <div
        v-show="section === 'task'"
        id="automation-task-panel"
        role="tabpanel"
        aria-labelledby="automation-task-tab"
      >
        <q-card flat class="app-surface">
          <q-card-section>
            <div class="text-subtitle1 text-weight-bold">Auftrag</div>
            <div class="text-caption app-muted">Name, Aufgabe und optionalen Skill festlegen.</div>
          </q-card-section>
          <q-separator />
          <q-card-section class="q-gutter-md">
            <q-input v-model="form.name" outlined autofocus label="Name" maxlength="240" counter lazy-rules :rules="[requiredRule]" />
            <q-input
              v-model="form.task"
              outlined
              type="textarea"
              autogrow
              label="Auftrag"
              maxlength="40000"
              counter
              lazy-rules
              :rules="[requiredRule]"
              hint="Beschreibe das gewünschte Ergebnis eindeutig."
            />
            <q-select
              v-model="form.skillId"
              outlined
              emit-value
              map-options
              clearable
              label="Skill (optional)"
              :options="skillOptions"
              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
            />
            <q-toggle v-model="form.enabled" color="primary" label="Automation aktiv" />
            <q-banner rounded class="app-surface-tonal">
              <template #avatar><q-icon name="pause_circle_outline" color="primary" /></template>
              Eine deaktivierte Automation pausiert ihre Zeitpläne. Manuelle Läufe bleiben möglich.
            </q-banner>
          </q-card-section>
        </q-card>
      </div>

      <div
        v-show="section === 'runtime'"
        id="automation-runtime-panel"
        role="tabpanel"
        aria-labelledby="automation-runtime-tab"
      >
        <q-card flat class="app-surface">
          <q-card-section>
            <div class="text-subtitle1 text-weight-bold">Laufzeit und Ausgabe</div>
            <div class="text-caption app-muted">Ausführungskontext übernehmen und Ergebnisse einer Session zuordnen.</div>
          </q-card-section>
          <q-separator />
          <q-card-section class="q-gutter-md">
            <template v-if="!isCreate">
              <q-toggle v-model="form.copyRuntime" color="primary" label="Laufzeit erneut aus Session übernehmen" />
              <q-select
                v-if="form.copyRuntime"
                v-model="form.sourceSessionId"
                outlined
                emit-value
                map-options
                clearable
                label="Quell-Session"
                :options="sessionOptions"
                :rules="[requiredRule]"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
              />
            </template>
            <q-select
              v-else
              v-model="form.sourceSessionId"
              outlined
              emit-value
              map-options
              clearable
              label="Laufzeit aus Session übernehmen (optional)"
              :options="sessionOptions"
              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
              hint="Übernimmt Modell, Memory, Websuche, Computer und aktuellen Ordner."
            />
            <q-select
              v-model="form.outputSessionId"
              outlined
              emit-value
              map-options
              clearable
              label="Ausgabe-Session (optional)"
              :options="sessionOptions"
              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
              hint="Ergebnisse werden zusätzlich in diese Session geschrieben."
              @update:model-value="clearOutputRequirement"
            />
            <q-toggle
              v-if="form.outputSessionId"
              v-model="form.outputSessionRequired"
              color="primary"
              label="Ausgabe-Session als Pflichtbindung schützen"
            />
          </q-card-section>
        </q-card>
      </div>

      <div
        v-show="section === 'schedules'"
        id="automation-schedules-panel"
        role="tabpanel"
        aria-labelledby="automation-schedules-tab"
      >
        <q-card flat class="app-surface">
          <q-card-section class="row items-center justify-between q-gutter-md">
            <div>
              <div class="text-subtitle1 text-weight-bold">Zeitpläne</div>
              <div class="text-caption app-muted">Wiederkehrend per Cron oder einmalig zu einem festen Zeitpunkt.</div>
            </div>
            <q-btn
              outline
              no-caps
              color="primary"
              icon="add"
              label="Zeitplan hinzufügen"
              :disable="form.schedules.length >= 50"
              @click="addSchedule"
            />
          </q-card-section>
          <q-separator />
          <q-card-section>
            <q-card v-for="(schedule, index) in form.schedules" :key="index" flat class="app-surface-tonal q-mb-md">
              <q-card-section class="row q-col-gutter-sm items-start">
                <div class="col-12 col-md-3">
                  <q-select
                    v-model="schedule.kind"
                    outlined
                    dense
                    emit-value
                    map-options
                    label="Ausführung"
                    :options="scheduleKindOptions"
                    :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                  />
                </div>
                <div v-if="schedule.kind === 'cron'" class="col-12 col-md-4">
                  <q-input
                    v-model="schedule.cron"
                    outlined
                    dense
                    :label="`Cron ${index + 1}`"
                    maxlength="255"
                    lazy-rules
                    :rules="[requiredRule, cronRule]"
                    hint="Minute Stunde Tag Monat Wochentag"
                  />
                </div>
                <div v-if="schedule.kind === 'cron'" class="col-12 col-md-4">
                  <q-input
                    v-model="schedule.timezone"
                    outlined
                    dense
                    label="IANA-Zeitzone"
                    maxlength="100"
                    lazy-rules
                    :rules="[requiredRule]"
                    hint="Zum Beispiel Europe/Berlin"
                  />
                </div>
                <div v-else class="col-12 col-md-8">
                  <q-input
                    v-model="schedule.runAt"
                    outlined
                    dense
                    stack-label
                    type="datetime-local"
                    label="Ausführungszeitpunkt"
                    lazy-rules
                    :rules="[requiredRule]"
                    hint="Lokale Uhrzeit dieses Geräts"
                  />
                </div>
                <div class="col-12 col-md-1 row justify-end">
                  <q-btn
                    flat
                    round
                    dense
                    color="negative"
                    icon="delete_outline"
                    :disable="form.schedules.length === 1"
                    :aria-label="`Zeitplan ${index + 1} entfernen`"
                    @click="form.schedules.splice(index, 1)"
                  />
                </div>
                <div v-if="schedule.kind === 'cron'" class="col-12 col-md-6">
                  <q-input
                    v-model="schedule.validUntil"
                    outlined
                    dense
                    stack-label
                    type="datetime-local"
                    label="Gültig bis (optional)"
                    hint="Lokale Uhrzeit dieses Geräts, letzter Termin inklusive"
                  />
                </div>
                <div class="col-12 col-md-6">
                  <q-toggle v-model="schedule.enabled" dense color="primary" label="Zeitplan aktiv" />
                </div>
              </q-card-section>
            </q-card>
            <q-toggle
              v-if="canArchiveAfterSuccess"
              v-model="form.archiveAfterSuccess"
              dense
              color="primary"
              label="Nach erfolgreicher Ausführung archivieren"
            />
          </q-card-section>
        </q-card>
      </div>

      <div class="row justify-end q-gutter-sm q-py-md">
        <q-btn flat no-caps label="Zurück" :disable="saving" :to="{ name: 'settings-automations' }" />
        <q-btn
          unelevated
          no-caps
          type="submit"
          color="primary"
          icon="save"
          label="Speichern"
          :loading="saving"
          :disable="loading || blocked || !dirty"
        />
      </div>
    </q-form>

    <q-inner-loading :showing="loading" />
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { formatDateTimeLocalInput, localDateTimeToIso } from 'src/utils/date-time'
import { useAutomationStore } from 'stores/automations'
import { useChatStore } from 'stores/chat'
import { useSkillStore } from 'stores/skills'

defineOptions({ name: 'AutomationEditorPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const automations = useAutomationStore()
const chat = useChatStore()
const skills = useSkillStore()
const formRef = ref(null)
const editingAutomation = ref(null)
const loading = ref(false)
const saving = ref(false)
const loadFailed = ref(false)
const notFound = ref(false)
const sessionLoadFailed = ref(false)
const sessionsLoading = ref(false)
const savedPayload = ref('')
const section = ref('task')
let loadToken = 0

const form = reactive({
  name: '', task: '', skillId: null, enabled: true, sourceSessionId: null, copyRuntime: false,
  outputSessionId: null, outputSessionRequired: false, archiveAfterSuccess: false, schedules: []
})
const scheduleKindOptions = [
  { label: 'Wiederkehrend', value: 'cron' },
  { label: 'Einmalig', value: 'once' }
]
const isCreate = computed(() => route.name === 'settings-automation-create')
const blocked = computed(() => Boolean(editingAutomation.value?.archivedAt))
const canArchiveAfterSuccess = computed(() => (
  form.schedules.length === 1 && form.schedules[0]?.kind === 'once'
))
const sessionOptions = computed(() => {
  const options = chat.sessionCatalog.map((session) => ({ label: session.title, value: session.id }))
  if (form.outputSessionId && !options.some((option) => option.value === form.outputSessionId)) {
    options.push({ label: 'Session nicht verfügbar', value: form.outputSessionId })
  }
  return options
})
const skillOptions = computed(() => skills.items.map((skill) => ({ label: skill.name, value: skill.id })))
const dirty = computed(() => Boolean(savedPayload.value) && JSON.stringify(requestPayload()) !== savedPayload.value)

const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
const cronRule = (value) => String(value || '').trim().split(/\s+/).length === 5 || 'Cron muss genau fünf Felder enthalten.'

function blankSchedule() {
  return { kind: 'cron', cron: '0 8 * * 1-5', timezone: 'Europe/Berlin', runAt: '', validUntil: '', enabled: true }
}

function requestPayload() {
  return {
    name: form.name.trim(),
    task: form.task.trim(),
    skillId: form.skillId || null,
    enabled: form.enabled,
    sourceSessionId: editingAutomation.value && !form.copyRuntime ? null : form.sourceSessionId || null,
    outputSessionId: form.outputSessionId || null,
    outputSessionRequired: Boolean(form.outputSessionId && form.outputSessionRequired),
    archiveAfterSuccess: Boolean(canArchiveAfterSuccess.value && form.archiveAfterSuccess),
    schedules: form.schedules.map((schedule) => schedule.kind === 'once'
      ? { runAt: localDateTimeToIso(schedule.runAt), enabled: schedule.enabled }
      : {
          cron: schedule.cron.trim(),
          timezone: schedule.timezone.trim(),
          validUntil: schedule.validUntil ? localDateTimeToIso(schedule.validUntil) : null,
          enabled: schedule.enabled
        })
  }
}

function assignForm(automation = null) {
  editingAutomation.value = automation
  Object.assign(form, {
    name: automation?.name || '',
    task: automation?.task || '',
    skillId: automation?.skillId || null,
    enabled: automation?.enabled ?? true,
    sourceSessionId: null,
    copyRuntime: false,
    outputSessionId: automation?.outputSessionId || null,
    outputSessionRequired: automation?.outputSessionRequired || false,
    archiveAfterSuccess: automation?.archiveAfterSuccess || false,
    schedules: automation?.schedules?.map(({ cron, timezone, runAt, validUntil, enabled }) => ({
      kind: runAt ? 'once' : 'cron',
      cron: cron || '0 8 * * 1-5',
      timezone: timezone || 'Europe/Berlin',
      runAt: formatDateTimeLocalInput(runAt),
      validUntil: formatDateTimeLocalInput(validUntil),
      enabled
    })) || [blankSchedule()]
  })
  savedPayload.value = JSON.stringify(requestPayload())
}

function addSchedule() {
  form.schedules.push(blankSchedule())
}

function clearOutputRequirement(value) {
  if (!value) form.outputSessionRequired = false
}

function invalidSection() {
  if (!form.name.trim() || !form.task.trim()) return 'task'
  if (!isCreate.value && form.copyRuntime && !form.sourceSessionId) return 'runtime'
  if (form.schedules.some((schedule) => schedule.kind === 'once'
    ? !schedule.runAt
    : !schedule.cron.trim() || schedule.cron.trim().split(/\s+/).length !== 5 || !schedule.timezone.trim())) {
    return 'schedules'
  }
  return null
}

async function loadSessions() {
  sessionsLoading.value = true
  sessionLoadFailed.value = false
  try {
    await chat.fetchSessionCatalog({ suppressErrorToast: true })
  } catch {
    sessionLoadFailed.value = true
  } finally {
    sessionsLoading.value = false
  }
}

async function load() {
  const token = ++loadToken
  editingAutomation.value = null
  savedPayload.value = ''
  loadFailed.value = false
  notFound.value = false
  loading.value = true
  section.value = 'task'
  void skills.fetchSkills().catch(() => {})
  void loadSessions()
  try {
    if (isCreate.value) {
      assignForm()
      return
    }
    const automation = await automations.fetchAutomation(String(route.params.automationId || ''), { suppressErrorToast: true })
    if (token === loadToken) assignForm(automation)
  } catch (error) {
    if (token !== loadToken) return
    notFound.value = error?.response?.status === 404
    loadFailed.value = true
  } finally {
    if (token === loadToken) loading.value = false
  }
}

async function saveAutomation() {
  if (loading.value || saving.value || loadFailed.value || blocked.value || !dirty.value) return
  section.value = invalidSection() || section.value
  if (!await formRef.value?.validate()) return
  saving.value = true
  const creating = isCreate.value
  try {
    const payload = requestPayload()
    const saved = editingAutomation.value
      ? await automations.updateAutomation(editingAutomation.value.id, {
          ...payload, expectedRevision: editingAutomation.value.revision
        })
      : await automations.createAutomation(payload)
    assignForm(saved)
    if (creating) {
      await router.replace({
        name: 'settings-automation-edit',
        params: { automationId: saved.id }
      })
    }
    $q.notify({ type: 'positive', message: creating ? 'Automation erstellt' : 'Automation gespeichert' })
  } catch {
    // The central Axios interceptor displays the error; keep the editor open.
  } finally {
    saving.value = false
  }
}

async function confirmDiscardedChanges() {
  if (!dirty.value) return true
  if (saving.value) return false
  return confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
}

function preventDiscardOnUnload(event) {
  if (!dirty.value && !saving.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  void load()
  window.addEventListener('beforeunload', preventDiscardOnUnload)
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', preventDiscardOnUnload))
watch(() => [route.name, route.params.automationId], () => void load())
onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
