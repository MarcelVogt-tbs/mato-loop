<template>
  <section class="column full-height no-wrap" aria-labelledby="automation-app-title">
    <header class="row items-center no-wrap q-gutter-sm q-pa-md">
      <q-btn v-if="selectedId" flat round dense icon="arrow_back" aria-label="Zurück zu den Automationen" @click="select(null)" />
      <div class="col overflow-hidden">
        <h2 id="automation-app-title" class="text-subtitle1 text-weight-bold q-my-none ellipsis" :title="definition?.name">
          {{ selectedId ? definition?.name || 'Neue Automation' : 'Automationen' }}
        </h2>
        <div v-if="definition" class="text-caption app-muted" role="status">
          <q-icon :name="status.icon" :color="status.color" class="q-mr-xs" />{{ status.label }}
        </div>
        <div v-else-if="!selectedId" class="text-caption app-muted">Aufträge in dieser Session</div>
      </div>
      <q-btn v-if="!selectedId && entries.length" unelevated no-caps color="primary" icon="add" label="Neu" @click="select('new')" />
    </header>
    <q-separator />
    <q-scroll-area class="col">
      <div class="q-pa-md">
        <q-banner v-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
          Die Automation konnte nicht geladen werden.
          <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
        </q-banner>
        <div v-if="loading" class="row justify-center q-pa-md" role="status"><q-spinner color="primary" size="24px" /><span class="app-sr-only">Automationen werden geladen</span></div>
        <template v-else-if="!selectedId">
          <div v-if="!entries.length && !loadFailed" class="text-center q-py-lg">
            <q-icon name="schedule" size="36px" color="primary" />
            <p class="text-body2 q-mt-sm">Was soll der Agent für dich übernehmen?</p>
            <q-btn unelevated no-caps color="primary" icon="add" label="Automation erstellen" @click="select('new')" />
          </div>
          <q-list v-else separator>
            <q-item v-for="item in entries" :key="item.id" clickable v-ripple class="q-px-none q-py-sm" @click="select(item.id)">
              <q-item-section avatar><q-icon :name="automationStatus(item).icon" :color="automationStatus(item).color" /></q-item-section>
              <q-item-section>
                <q-item-label lines="1" class="text-weight-medium">{{ item.name }}</q-item-label>
                <q-item-label caption>{{ timingLabel(item) }}</q-item-label>
                <q-item-label caption>{{ automationStatus(item).label }}</q-item-label>
              </q-item-section>
              <q-item-section side @click.stop @keydown.stop>
                <q-btn flat round dense icon="more_vert" :aria-label="`Aktionen für ${item.name}`" :disable="saving || starting">
                  <q-menu><q-list dense>
                    <q-item v-if="!item.archivedAt" clickable v-close-popup :disable="Boolean(item.effectiveState?.activeRunStatus)" @click="runNow(item)"><q-item-section>Jetzt ausführen</q-item-section></q-item>
                    <q-item v-if="!item.archivedAt && item.schedules.length" clickable v-close-popup @click="toggleEnabled(item)"><q-item-section>{{ item.enabled ? 'Pausieren' : 'Fortsetzen' }}</q-item-section></q-item>
                    <q-item clickable v-close-popup class="text-negative" @click="remove(item)"><q-item-section>Entfernen</q-item-section></q-item>
                  </q-list></q-menu>
                </q-btn>
              </q-item-section>
            </q-item>
          </q-list>
        </template>
        <template v-else-if="!loadFailed">
          <q-banner v-if="conflict" rounded class="app-surface-tonal q-mb-md">
            Die Automation wurde geändert. Deine Eingaben bleiben erhalten.
            <template #action><q-btn flat no-caps color="primary" label="Aktuellen Stand laden" @click="reload" /></template>
          </q-banner>
          <q-banner v-if="archived" rounded class="app-surface-tonal q-mb-md">Diese Automation ist archiviert. Das Gespräch bleibt erhalten.</q-banner>
          <q-form ref="formRef" :inert="saving ? '' : null" @submit.prevent="save">
            <div class="row items-center q-mb-sm">
              <h3 class="text-subtitle2 text-weight-bold q-my-none col">Auftrag</h3>
              <q-btn v-if="!archived && !editingTask" flat dense no-caps icon="edit" label="Bearbeiten" @click="editingTask = true" />
              <q-btn flat round dense icon="info_outline" aria-label="Hinweis zum Auftrag">
                <q-popup-proxy><q-banner class="app-surface">Der gespeicherte Auftrag wird in diesem Gespräch ausgeführt. Chatnachrichten ändern ihn nicht automatisch, können aber spätere Antworten beeinflussen.</q-banner></q-popup-proxy>
              </q-btn>
            </div>
            <div v-if="editingTask" class="q-gutter-sm">
              <q-input v-model="form.name" :readonly="archived" outlined dense label="Name" maxlength="240" hide-bottom-space lazy-rules :rules="[requiredRule]" />
              <q-input v-model="form.task" :readonly="archived" outlined dense type="textarea" rows="6" label="Was soll der Agent tun?" maxlength="40000" hide-bottom-space lazy-rules :rules="[requiredRule]" />
            </div>
            <template v-else>
              <q-item-label :lines="taskExpanded ? undefined : 3" class="text-body2 text-pre-wrap">{{ form.task }}</q-item-label>
              <q-btn flat dense no-caps color="primary" :label="taskExpanded ? 'Weniger anzeigen' : 'Vollständigen Auftrag anzeigen'" class="q-mt-xs" @click="taskExpanded = !taskExpanded" />
            </template>

            <q-separator class="q-my-md" />
            <div class="row items-center q-mb-sm">
              <h3 class="text-subtitle2 text-weight-bold q-my-none col">Zeitpläne</h3>
              <q-btn v-if="!archived" flat dense no-caps color="primary" icon="add" label="Hinzufügen" :disable="form.schedules.length >= 50" @click="addSchedule" />
            </div>
            <p v-if="!form.schedules.length" class="text-body2 app-muted">Nur auf Knopfdruck – kein automatischer Start.</p>
            <q-list v-else bordered separator class="rounded-borders">
              <AutomationScheduleEditor v-for="(schedule, index) in form.schedules" :key="`${selectedId}-${index}`" v-model="form.schedules[index]" :expanded="expandedSchedule === index" :readonly="archived" :error="validationAttempted ? scheduleError(schedule) : ''" @update:expanded="expandedSchedule = $event ? index : expandedSchedule === index ? null : expandedSchedule" @remove="removeSchedule(index)" />
            </q-list>
            <div v-if="definition && savedState.effectiveState?.nextRunAt" class="text-caption app-muted q-mt-sm">{{ automationTiming(savedState) }}</div>

            <q-expansion-item v-model="optionsOpen" dense label="Weitere Optionen" class="q-mt-md">
              <div class="q-pa-sm q-gutter-sm">
                <q-select v-model="form.skillId" :readonly="archived" outlined dense emit-value map-options clearable label="Skill verwenden (optional)" :options="skillOptions" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" />
                <q-toggle v-if="canArchive" v-model="form.archiveAfterSuccess" :disable="archived" dense label="Nach erfolgreicher Ausführung archivieren" />
                <q-btn v-if="definition" flat dense no-caps color="negative" icon="delete_outline" label="Automation entfernen" :disable="saving" @click="remove(definition)" />
              </div>
            </q-expansion-item>
          </q-form>
          <q-expansion-item v-if="definition" v-model="historyOpen" dense label="Bisherige Ausführungen">
            <AutomationRunHistory v-if="historyOpen" :key="definition.id" :automation-id="definition.id" :session-id="sessionId" :visible="controlCenter.activeAppId === 'automation'" :run-id="String(route.query.run || '')" />
          </q-expansion-item>
        </template>
      </div>
    </q-scroll-area>
    <template v-if="selectedId && !loading && !loadFailed">
      <q-separator />
      <footer class="q-pa-sm" aria-label="Automationsaktionen">
        <div v-if="dirty" class="text-caption app-muted q-px-sm q-pb-xs">Ungespeicherte Änderungen</div>
        <div class="row items-center q-gutter-xs">
          <q-btn v-if="dirty || !definition" unelevated no-caps no-wrap color="primary" label="Speichern" :loading="saving" :disable="archived || conflict" @click="save" />
          <q-btn v-if="definition" :unelevated="!dirty" :flat="dirty" no-caps no-wrap color="primary" icon="play_arrow" label="Jetzt ausführen" :loading="starting" :disable="dirty || archived || conflict || busy || saving" @click="runNow(definition)" />
          <q-btn v-if="definition?.schedules.length && !archived" flat no-caps no-wrap :icon="savedState.enabled ? 'pause' : 'play_arrow'" :label="savedState.enabled ? 'Pausieren' : 'Fortsetzen'" :disable="dirty || conflict || saving" @click="toggleEnabled(definition)" />
        </div>
      </footer>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import AutomationScheduleEditor from 'src/features/automations/AutomationScheduleEditor.vue'
import AutomationRunHistory from 'src/features/automations/AutomationRunHistory.vue'
import { useConfirm } from 'src/composables/confirm'
import { scheduleDraft, scheduleError, schedulePayload, scheduleSummary } from 'src/utils/automation-schedules'
import { automationStatus, automationTiming } from 'src/utils/automation-status'
import { isPwaUpdateReload } from 'src/utils/pwa-update'
import { useAutomationStore } from 'stores/automations'
import { useControlCenterStore } from 'stores/control-center'
import { useSkillStore } from 'stores/skills'

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const automations = useAutomationStore()
const controlCenter = useControlCenterStore()
const skills = useSkillStore()
const sessionId = computed(() => controlCenter.sessionId)
const selectedId = computed(() => typeof route.query.automation === 'string' ? route.query.automation : null)
const definition = ref(null)
const formRef = ref(null)
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const starting = ref(false)
const savedDraft = ref('')
const validationAttempted = ref(false)
const historyOpen = ref(false)
const optionsOpen = ref(false)
const editingTask = ref(false)
const taskExpanded = ref(false)
const expandedSchedule = ref(null)
const form = reactive({ name: '', task: '', enabled: true, schedules: [], skillId: null, archiveAfterSuccess: false })
const summaries = computed(() => new Map(automations.items.map((item) => [item.id, item])))
const entries = computed(() => automations.sessionItems
  .filter((item) => item.executionSessionId === sessionId.value)
  .map((item) => ({ ...item, ...summaries.value.get(item.id) })))
const summary = computed(() => summaries.value.get(definition.value?.id))
const savedState = computed(() => summary.value || definition.value)
const status = computed(() => automationStatus(savedState.value))
const archived = computed(() => Boolean(savedState.value?.archivedAt))
const conflict = computed(() => Boolean(definition.value && summary.value && summary.value.revision !== definition.value.revision))
const busy = computed(() => Boolean(savedState.value?.effectiveState?.activeRunStatus))
const canArchive = computed(() => form.schedules.length === 1 && form.schedules[0].kind === 'once')
const draftSignature = () => JSON.stringify(form, (key, value) => key === 'advanced' ? undefined : value)
const dirty = computed(() => Boolean(selectedId.value && savedDraft.value) && draftSignature() !== savedDraft.value)
const skillOptions = computed(() => {
  const options = skills.items.map((skill) => ({ label: skill.name, value: skill.id }))
  if (form.skillId && !options.some((item) => item.value === form.skillId)) options.push({ label: 'Gespeicherter Skill nicht verfügbar', value: form.skillId })
  return options
})
const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
let version = 0

function timingLabel(item) {
  if (!item.schedules.length) return 'Nur auf Knopfdruck'
  return scheduleSummary(scheduleDraft(item.schedules[0])) + (item.schedules.length > 1 ? ` · +${item.schedules.length - 1} weitere` : '')
}

function assign(value) {
  definition.value = value
  Object.assign(form, {
    name: value?.name || '', task: value?.task || '', enabled: value?.enabled ?? true,
    schedules: value?.schedules?.map(scheduleDraft) || [],
    skillId: value?.skillId || null, archiveAfterSuccess: value?.archiveAfterSuccess || false
  })
  savedDraft.value = draftSignature()
  validationAttempted.value = false
  editingTask.value = !value
}

async function load() {
  const current = ++version
  loading.value = true
  loadFailed.value = false
  savedDraft.value = ''
  definition.value = null
  saving.value = false
  starting.value = false
  expandedSchedule.value = null
  taskExpanded.value = false
  historyOpen.value = Boolean(route.query.run)
  optionsOpen.value = false
  try {
    const values = await automations.fetchForSession(sessionId.value)
    if (current !== version) return
    const value = values.find((item) => item.id === selectedId.value)
    if (selectedId.value && selectedId.value !== 'new' && !value) { loadFailed.value = true; return }
    assign(value || null)
  } catch {
    if (current === version) loadFailed.value = true
  } finally {
    if (current === version) loading.value = false
  }
}

async function select(id) {
  await router.replace({ query: { ...route.query, automation: id || undefined, run: undefined } })
}

function payload() {
  return {
    name: form.name.trim(), task: form.task.trim(), sessionId: sessionId.value,
    skillId: form.skillId, enabled: form.enabled, schedules: form.schedules.map(schedulePayload),
    archiveAfterSuccess: Boolean(canArchive.value && form.archiveAfterSuccess)
  }
}

async function save() {
  if (saving.value || loading.value || archived.value || conflict.value || !selectedId.value) return
  const current = version
  saving.value = true
  validationAttempted.value = true
  try {
    if (!form.name.trim() || !form.task.trim()) { editingTask.value = true; await nextTick() }
    const valid = await formRef.value?.validate()
    const invalidIndex = form.schedules.findIndex((schedule) => scheduleError(schedule))
    if (invalidIndex !== -1) { expandedSchedule.value = invalidIndex; form.schedules[invalidIndex].advanced = true }
    if (current !== version || !valid || invalidIndex !== -1) return
    const value = definition.value
      ? await automations.updateAutomation(definition.value.id, { ...payload(), expectedRevision: definition.value.revision })
      : await automations.createAutomation(payload())
    if (current !== version) return
    assign(value)
    saving.value = false
    await controlCenter.ensureApp('automation')
    if (selectedId.value === 'new') await select(value.id)
    $q.notify({ type: 'positive', message: 'Automation gespeichert' })
  } catch {
    // The Axios interceptor displays the error; keep the draft.
  } finally {
    if (current === version) saving.value = false
  }
}

async function runNow(item = definition.value) {
  if (!item || item.effectiveState?.activeRunStatus || dirty.value || item.archivedAt || starting.value || saving.value || conflict.value) return
  const current = version
  starting.value = true
  try { await automations.runNow(item.id, item.revision) } catch {
    // The Axios interceptor displays the error.
  } finally { if (current === version) starting.value = false }
}

async function toggleEnabled(item = definition.value) {
  if (!item || saving.value || dirty.value || conflict.value) return
  const current = version
  saving.value = true
  try {
    const saved = await automations.fetchAutomation(item.id)
    if (current !== version) return
    const value = await automations.updateAutomation(item.id, {
      name: saved.name, task: saved.task, skillId: saved.skillId, enabled: !item.enabled,
      schedules: saved.schedules.map((schedule) => schedulePayload(scheduleDraft(schedule))),
      archiveAfterSuccess: saved.archiveAfterSuccess || false, expectedRevision: saved.revision
    })
    if (current === version && definition.value?.id === item.id) assign(value)
  } catch {
    // The Axios interceptor displays the error; keep the saved state.
  } finally { if (current === version) saving.value = false }
}

async function remove(item = definition.value) {
  const current = version
  if (!item || !await confirm(`„${item.name}“ mit allen Zeitplänen entfernen? Das Gespräch bleibt erhalten.`, 'Automation entfernen?', { okLabel: 'Entfernen' }) || current !== version) return
  saving.value = true
  try {
    await automations.deleteAutomation(item.id, item.revision)
    if (current !== version) return
    if (definition.value?.id === item.id) { assign(null); saving.value = false; await select(null) }
    $q.notify({ type: 'positive', message: 'Automation entfernt' })
  } catch {
    // The Axios interceptor displays the error.
  } finally { if (current === version) saving.value = false }
}

function addSchedule() {
  form.schedules.push(scheduleDraft())
  expandedSchedule.value = form.schedules.length - 1
}

async function removeSchedule(index) {
  const current = version
  if (await confirm('Diesen Zeitplan entfernen?', 'Zeitplan entfernen?', { okLabel: 'Entfernen' }) && current === version) {
    form.schedules.splice(index, 1)
    expandedSchedule.value = null
  }
}

async function confirmDiscard() {
  if (saving.value) return false
  return !dirty.value || await confirm('Ungespeicherte Änderungen verwerfen?', 'Änderungen verwerfen?', { okLabel: 'Verwerfen' })
}

async function reload() {
  const current = version
  if (await confirmDiscard() && current === version) await load()
}

function beforeUnload(event) {
  if (isPwaUpdateReload() || (!dirty.value && !saving.value)) return
  event.preventDefault()
  event.returnValue = ''
}

onBeforeRouteUpdate((to, from) => to.query.session !== from.query.session || to.query.automation === from.query.automation || confirmDiscard())
watch(dirty, (value) => { automations.dirty = value }, { flush: 'sync' })
watch([sessionId, selectedId], () => { if (sessionId.value) void load(); else version += 1 }, { immediate: true })
watch(() => automations.items.filter((item) => item.executionSessionId === sessionId.value).map((item) => `${item.id}:${item.revision}`).join(','), () => {
  if (sessionId.value && !selectedId.value && !loading.value && !saving.value) void load()
})
void skills.fetchSkills().catch(() => {})
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => { version += 1; automations.dirty = false; window.removeEventListener('beforeunload', beforeUnload) })
</script>
