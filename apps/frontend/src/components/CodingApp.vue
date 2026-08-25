<template>
  <section class="column full-height no-wrap" aria-labelledby="coding-app-title">
    <ControlCenterContentHeader
      icon="code"
      title="OpenCode"
      title-id="coding-app-title"
      integration-id="coding"
      refreshable
      refresh-label="Coding-Daten aktualisieren"
      :refreshing="coding.loading"
      :can-move-left="integrationIndex > 0"
      :can-move-right="integrationIndex >= 0 && integrationIndex < controlCenter.tabs.length - 1"
      @refresh="refresh"
      @close="emit('close')"
      @remove="emit('remove', 'coding')"
      @move="emit('move', 'coding', $event)"
    />

    <div v-if="!available" class="col column flex-center text-center q-pa-xl">
      <q-icon name="code_off" size="52px" color="grey-6" />
      <div class="text-subtitle1 text-weight-medium q-mt-md">OpenCode ist für dieses Projekt nicht verfügbar</div>
      <div class="text-body2 app-muted q-mt-xs">Öffne eine Session in einem Projekt mit OpenCode-Zuordnung.</div>
    </div>
    <div v-else-if="coding.loadFailed && !coding.loaded" class="col column flex-center text-center q-pa-xl">
      <q-icon name="cloud_off" size="52px" color="grey-6" />
      <div class="text-subtitle1 text-weight-medium q-mt-md">Coding-Daten konnten nicht geladen werden</div>
      <q-btn outline no-caps color="primary" label="Erneut laden" class="q-mt-md" @click="refresh" />
    </div>
    <template v-else>
      <q-expansion-item icon="tune" label="Modelle" header-class="text-weight-medium">
        <q-card-section class="row q-col-gutter-sm q-pt-none">
          <div class="col-12 col-sm-5">
            <q-select
              v-model="analysisModel"
              outlined dense emit-value map-options
              label="Analysemodell"
              :options="modelOptions"
              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
            />
          </div>
          <div class="col-12 col-sm-5">
            <q-select
              v-model="executionModel"
              outlined dense emit-value map-options
              label="Ausführungsmodell"
              :options="modelOptions"
              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
            />
          </div>
          <div class="col-12 col-sm-2 row items-center">
            <q-btn
              unelevated no-caps color="primary" label="Speichern" class="full-width"
              :disable="!analysisModel || !executionModel"
              :loading="savingModels"
              @click="saveModels"
            />
          </div>
        </q-card-section>
      </q-expansion-item>
      <q-separator />

      <q-tabs v-model="tab" dense no-caps align="left" active-color="primary" indicator-color="primary">
        <q-tab name="files" icon="folder_open" label="Dateien" />
        <q-tab name="plans" icon="description" label="Pläne" />
        <q-tab name="executions" icon="playlist_play" label="Ausführungen" />
      </q-tabs>
      <q-separator />

      <q-tab-panels v-model="tab" animated class="col bg-transparent">
        <q-tab-panel name="files" class="q-pa-none column no-wrap">
          <template v-if="coding.content">
            <q-toolbar>
              <q-btn flat round dense icon="arrow_back" aria-label="Zurück zur Dateiliste" @click="coding.closeFile()" />
              <q-toolbar-title class="text-subtitle2 ellipsis">{{ coding.content.path }}</q-toolbar-title>
            </q-toolbar>
            <q-separator />
            <q-scroll-area class="col">
              <pre v-if="coding.content.type === 'text'" class="coding-text q-ma-none q-pa-md">{{ coding.content.content }}</pre>
              <div v-else-if="binaryImage" class="q-pa-md"><q-img :src="binaryImage" fit="contain" /></div>
              <div v-else class="column flex-center text-center q-pa-xl app-muted">
                <q-icon name="insert_drive_file" size="48px" />
                <div class="q-mt-sm">Für diese Binärdatei ist keine Vorschau verfügbar.</div>
              </div>
            </q-scroll-area>
          </template>
          <template v-else>
            <q-toolbar>
              <q-btn
                flat round dense icon="arrow_upward" aria-label="Übergeordneten Ordner öffnen"
                :disable="coding.currentPath === '.'"
                @click="coding.loadFiles(parentPath(coding.currentPath))"
              />
              <q-toolbar-title class="text-subtitle2 ellipsis">{{ coding.currentPath }}</q-toolbar-title>
            </q-toolbar>
            <q-separator />
            <q-scroll-area class="col">
              <q-list separator>
                <q-item v-if="!coding.files.length && !coding.loading" class="q-pa-lg">
                  <q-item-section class="text-center app-muted">Dieser Ordner ist leer.</q-item-section>
                </q-item>
                <q-item
                  v-for="entry in coding.files"
                  :key="entry.path"
                  clickable v-ripple
                  @click="entry.type === 'directory' ? coding.loadFiles(entry.path) : coding.readFile(entry.path)"
                >
                  <q-item-section avatar><q-icon :name="entry.type === 'directory' ? 'folder' : 'description'" /></q-item-section>
                  <q-item-section><q-item-label lines="1">{{ entry.name }}</q-item-label></q-item-section>
                  <q-item-section side>
                    <div class="row q-gutter-xs">
                      <q-chip v-if="entry.ignored" dense outline color="grey-7">Ignoriert</q-chip>
                      <q-chip v-if="statusByPath.get(entry.path)" dense :color="statusColor(statusByPath.get(entry.path).status)" :text-color="statusTextColor(statusByPath.get(entry.path).status)">
                        {{ statusLabel(statusByPath.get(entry.path).status) }}
                      </q-chip>
                    </div>
                  </q-item-section>
                </q-item>
                <q-item v-for="entry in deletedStatuses" :key="`deleted:${entry.path}`">
                  <q-item-section avatar><q-icon name="delete_outline" /></q-item-section>
                  <q-item-section><q-item-label lines="1">{{ fileName(entry.path) }}</q-item-label></q-item-section>
                  <q-item-section side>
                    <q-chip dense color="negative" text-color="white">Gelöscht</q-chip>
                  </q-item-section>
                </q-item>
              </q-list>
            </q-scroll-area>
          </template>
        </q-tab-panel>

        <q-tab-panel name="plans" class="q-pa-none column no-wrap">
          <q-toolbar>
            <q-toolbar-title class="text-subtitle2">Gespeicherte Pläne</q-toolbar-title>
            <q-btn
              unelevated no-caps color="primary" icon="playlist_add" label="Ausgewählte einreihen"
              :disable="!selectedPlans.length"
              :loading="enqueueing"
              @click="enqueue"
            />
          </q-toolbar>
          <q-separator />
          <q-scroll-area class="col">
            <q-list separator>
              <q-item v-if="!coding.plans.length" class="q-pa-lg">
                <q-item-section class="text-center app-muted">Noch keine Pläne gespeichert. Erstelle sie im Projektchat.</q-item-section>
              </q-item>
              <q-expansion-item v-for="plan in coding.plans" :key="plan.id" group="coding-plans">
                <template #header>
                  <q-item-section avatar>
                    <q-checkbox v-model="selectedPlans" :val="plan.id" :aria-label="`Plan ${plan.title} auswählen`" @click.stop />
                  </q-item-section>
                  <q-item-section>
                    <q-item-label>{{ plan.title }}</q-item-label>
                    <q-item-label caption>{{ formatAbsoluteDateTime(plan.createdAt) }}</q-item-label>
                  </q-item-section>
                </template>
                <q-card-section><MessageContent :content="plan.markdown" :allow-images="false" :interactive="false" /></q-card-section>
              </q-expansion-item>
            </q-list>
          </q-scroll-area>
        </q-tab-panel>

        <q-tab-panel name="executions" class="q-pa-none column no-wrap">
          <q-banner v-if="coding.queuePauseReason" dense class="app-surface-tonal">
            Warteschlange pausiert: {{ pauseReasonLabel(coding.queuePauseReason) }}
          </q-banner>
          <q-toolbar>
            <q-toolbar-title class="text-subtitle2">FIFO-Warteschlange</q-toolbar-title>
            <q-btn
              flat no-caps :icon="coding.queuePauseReason ? 'play_arrow' : 'pause'"
              :label="coding.queuePauseReason ? 'Fortsetzen' : 'Pausieren'"
              :loading="queueChanging"
              @click="toggleQueue"
            />
          </q-toolbar>
          <q-separator />
          <q-scroll-area class="col">
            <q-list separator>
              <q-item v-if="!coding.executions.length" class="q-pa-lg">
                <q-item-section class="text-center app-muted">Die Warteschlange ist leer.</q-item-section>
              </q-item>
              <q-item v-for="execution in coding.executions" :key="execution.id" class="q-py-md">
                <q-item-section>
                  <div class="row items-center q-gutter-sm">
                    <q-item-label class="text-weight-medium">Position {{ execution.queuePosition }}</q-item-label>
                    <q-chip dense :color="executionColor(execution.status)" :text-color="executionTextColor(execution.status)">{{ executionLabel(execution.status) }}</q-chip>
                  </div>
                  <q-item-label caption lines="1">Plan {{ execution.planId }} · {{ execution.modelKey }}</q-item-label>
                  <q-item-label v-if="execution.resultSummary" class="q-mt-sm" lines="3">{{ execution.resultSummary }}</q-item-label>
                  <q-item-label v-if="execution.errorCode" class="text-negative q-mt-sm">{{ apiErrorMessage({ error: { code: execution.errorCode } }) }}</q-item-label>
                  <q-banner v-if="execution.pendingPermission" dense rounded class="app-surface-tonal q-mt-sm">
                    <div class="text-weight-medium">{{ execution.pendingPermission.title }}</div>
                    <div class="text-caption coding-patterns">{{ execution.pendingPermission.patterns.join(', ') }}</div>
                    <div class="row q-gutter-sm q-mt-sm">
                      <q-btn dense unelevated no-caps color="primary" label="Einmal erlauben" :loading="permissionReplyingId === execution.id" :disable="permissionReplyingId === execution.id" @click="reply(execution.id, 'once')" />
                      <q-btn dense outline no-caps color="primary" label="Immer erlauben" :disable="permissionReplyingId === execution.id" @click="reply(execution.id, 'always')" />
                      <q-btn dense flat no-caps color="negative" label="Ablehnen" :disable="permissionReplyingId === execution.id" @click="reply(execution.id, 'reject')" />
                    </div>
                  </q-banner>
                </q-item-section>
                <q-item-section v-if="cancellable(execution.status)" side top>
                  <q-btn flat round dense color="negative" icon="cancel" aria-label="Ausführung abbrechen" @click="cancel(execution)" />
                </q-item-section>
              </q-item>
            </q-list>
          </q-scroll-area>
        </q-tab-panel>
      </q-tab-panels>
      <q-inner-loading :showing="coding.loading && !coding.loaded" />
    </template>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import ControlCenterContentHeader from 'components/ControlCenterContentHeader.vue'
import MessageContent from 'components/MessageContent.vue'
import { codingControlCenterAvailable } from 'src/control-center/manifest'
import { useConfirm } from 'src/composables/confirm'
import { parentPath } from 'src/features/workspace/state'
import { apiErrorMessage } from 'src/utils/api-errors'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useProjectCodingStore } from 'stores/project-coding'
import { useProjectStore } from 'stores/projects'

defineOptions({ name: 'CodingApp' })
const emit = defineEmits(['close', 'remove', 'move'])
const $q = useQuasar()
const { confirm } = useConfirm()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const projects = useProjectStore()
const coding = useProjectCodingStore()
const tab = ref('files')
const analysisModel = ref(null)
const executionModel = ref(null)
const savingModels = ref(false)
const enqueueing = ref(false)
const queueChanging = ref(false)
const selectedPlans = ref([])
const permissionReplyingId = ref(null)
let pollTimer = null

const projectId = computed(() => chat.current?.projectId || chat.sessionsProjectId || null)
const project = computed(() => projects.items.find((item) => item.id === projectId.value))
const available = computed(() => codingControlCenterAvailable(project.value))
const integrationIndex = computed(() => controlCenter.tabs.findIndex((item) => item.appId === 'coding'))
const modelOptions = computed(() => coding.models.map((model) => ({
  label: `${model.providerLabel} · ${model.label}`,
  value: model.key
})))
const statusByPath = computed(() => new Map(coding.statuses.map((item) => [item.path, item])))
const deletedStatuses = computed(() => coding.statuses.filter((item) => (
  item.status === 'deleted'
  && parentPath(item.path) === coding.currentPath
  && !coding.files.some((file) => file.path === item.path)
)))
const binaryImage = computed(() => (
  coding.content?.type === 'binary' && coding.content.mimeType?.startsWith('image/')
    ? `data:${coding.content.mimeType};base64,${coding.content.content}`
    : null
))

function statusLabel(status) {
  return { added: 'Neu', deleted: 'Gelöscht', modified: 'Geändert' }[status] || status
}

function statusColor(status) {
  return { added: 'positive', deleted: 'negative', modified: 'warning' }[status] || 'grey-7'
}

function statusTextColor(status) {
  return status === 'modified' ? 'dark' : 'white'
}

function fileName(path) {
  return path.split('/').at(-1)
}

function executionLabel(status) {
  return {
    queued: 'Vorgemerkt', running: 'Wird ausgeführt', waiting_permission: 'Freigabe erforderlich',
    completed: 'Abgeschlossen', failed: 'Fehlgeschlagen', cancelled: 'Abgebrochen',
    outcome_unknown: 'Ausgang unbekannt'
  }[status] || status
}

function executionColor(status) {
  return { completed: 'positive', failed: 'negative', cancelled: 'grey-7', outcome_unknown: 'warning' }[status] || 'primary'
}

function executionTextColor(status) {
  return status === 'outcome_unknown' ? 'dark' : 'white'
}

function pauseReasonLabel(reason) {
  return {
    manual: 'manuell', binding_changed: 'Zuordnung geändert', model_unavailable: 'Modell nicht verfügbar',
    external_activity: 'externe OpenCode-Aktivität', execution_failed: 'Ausführung fehlgeschlagen',
    outcome_unknown: 'Ausgang unbekannt'
  }[reason] || reason
}

function cancellable(status) {
  return ['queued', 'running', 'waiting_permission'].includes(status)
}

async function refresh(quiet = false) {
  if (!available.value || coding.loading) return
  try {
    await coding.refresh({ quiet })
  } catch {
    // Direct requests use the central toast; background polling stays quiet.
  }
}

async function saveModels() {
  savingModels.value = true
  try {
    await coding.saveModels(analysisModel.value, executionModel.value)
    $q.notify({ type: 'positive', message: 'Modellauswahl gespeichert.' })
  } catch {
    // The central API interceptor displays the error.
  } finally {
    savingModels.value = false
  }
}

async function enqueue() {
  enqueueing.value = true
  try {
    await coding.enqueue(selectedPlans.value)
    selectedPlans.value = []
    tab.value = 'executions'
    $q.notify({ type: 'positive', message: 'Pläne wurden eingereiht.' })
  } catch {
    // The central API interceptor displays the error.
  } finally {
    enqueueing.value = false
  }
}

async function toggleQueue() {
  queueChanging.value = true
  try {
    if (coding.queuePauseReason) await coding.resumeQueue()
    else await coding.pauseQueue()
  } catch {
    // The central API interceptor displays the error.
  } finally {
    queueChanging.value = false
  }
}

async function cancel(execution) {
  if (!await confirm('Diese Ausführung abbrechen?', 'Ausführung abbrechen?', { okLabel: 'Abbrechen', okColor: 'negative' })) return
  try {
    await coding.cancelExecution(execution.id)
  } catch {
    // The central API interceptor displays the error.
  }
}

async function reply(executionId, response) {
  if (permissionReplyingId.value) return
  permissionReplyingId.value = executionId
  try {
    await coding.replyPermission(executionId, response)
  } catch {
    // The central API interceptor displays the error.
  } finally {
    permissionReplyingId.value = null
  }
}

function updatePolling() {
  clearInterval(pollTimer)
  pollTimer = null
  if (controlCenter.open && controlCenter.activeContentId === 'coding' && available.value) {
    pollTimer = setInterval(() => void refresh(true), 5000)
  }
}

watch([projectId, available], async ([value, isAvailable]) => {
  coding.selectProject(value)
  selectedPlans.value = []
  if (isAvailable) await refresh()
  updatePolling()
}, { immediate: true })
watch(() => [coding.analysisModelKey, coding.executionModelKey], ([analysis, execution]) => {
  analysisModel.value = analysis
  executionModel.value = execution
}, { immediate: true })
watch(() => [controlCenter.open, controlCenter.activeContentId, available.value], updatePolling)
onBeforeUnmount(() => clearInterval(pollTimer))
</script>

<style scoped>
.coding-text {
  font-family: monospace;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.coding-patterns {
  overflow-wrap: anywhere;
}
</style>
