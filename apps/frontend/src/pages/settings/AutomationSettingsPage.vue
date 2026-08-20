<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Automationen</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Agent-Aufträge mit einmaligen oder wiederkehrenden Zeitplänen und eigener Laufhistorie verwalten.
        </p>
      </div>
      <div class="row items-center q-gutter-sm wrap">
        <q-toggle v-model="showArchived" color="primary" label="Archiv anzeigen" />
        <q-btn v-if="!showArchived" unelevated no-caps color="primary" icon="add" label="Automation erstellen" @click="openCreateDialog" />
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
    <q-card v-else-if="loaded && !automations.items.length" flat class="app-surface text-center q-pa-xl">
      <q-icon name="schedule" size="48px" color="grey-6" />
      <div class="text-subtitle1 text-weight-medium q-mt-sm">{{ showArchived ? 'Keine archivierten Automationen' : 'Noch keine Automationen' }}</div>
      <div v-if="!showArchived" class="text-body2 app-muted q-mt-xs">Erstelle einen Auftrag mit mindestens einem Zeitplan.</div>
    </q-card>
    <div v-else class="row q-col-gutter-md">
      <div v-for="automation in automations.items" :key="automation.id" class="col-12 col-lg-6">
        <q-card flat class="app-surface full-height column">
          <q-card-section class="row items-start no-wrap q-gutter-md">
            <q-avatar :color="automation.effectiveState?.enabled ? 'primary' : 'grey-6'" text-color="white" icon="schedule" />
            <div class="col">
              <div class="row items-center q-gutter-sm">
                <div class="text-subtitle1 text-weight-bold">{{ automation.name }}</div>
                <q-chip dense :color="stateColor(automation)" :text-color="statusTextColor(stateColor(automation))">{{ stateLabel(automation) }}</q-chip>
              </div>
              <div class="text-body2 app-muted q-mt-xs text-pre-wrap">{{ automation.task }}</div>
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
              flat no-caps icon="play_arrow" label="Jetzt ausführen"
              :loading="runningId === automation.id"
              :disable="Boolean(activeStatus(automation)) || Boolean(runningId)"
              @click="runNow(automation)"
            >
              <q-tooltip v-if="activeStatus(automation)">Es läuft bereits ein Auftrag.</q-tooltip>
            </q-btn>
            <q-btn flat no-caps icon="history" label="Details/Verlauf" @click="showDetail(automation.id)" />
            <q-btn flat round icon="more_vert" :aria-label="`Aktionen für ${automation.name}`">
              <q-menu class="app-action-menu">
                <q-list padding>
                  <q-item v-if="!automation.archivedAt" clickable v-ripple v-close-popup @click="openEditDialog(automation)">
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

    <q-dialog v-model="formDialogOpen" :maximized="$q.screen.lt.sm" :persistent="saving" aria-labelledby="automation-form-title">
      <q-card class="automation-dialog app-surface column no-wrap">
        <q-form ref="formRef" class="column no-wrap full-height" @submit.prevent="saveAutomation">
          <q-card-section class="row items-start no-wrap q-gutter-md">
            <q-avatar color="primary" text-color="white" :icon="editingAutomation ? 'edit_calendar' : 'add_task'" />
            <div class="col">
              <div id="automation-form-title" class="text-h6">{{ editingAutomation ? 'Automation bearbeiten' : 'Automation erstellen' }}</div>
              <div class="text-body2 app-muted">Auftrag, Laufzeit, Ausgabe und Zeitpläne festlegen.</div>
            </div>
            <q-btn flat round dense icon="close" aria-label="Dialog schließen" :disable="saving" v-close-popup />
          </q-card-section>
          <q-separator />
          <q-card-section class="col scroll q-gutter-md">
            <q-input v-model="form.name" outlined autofocus label="Name" maxlength="240" counter lazy-rules :rules="[requiredRule]" />
            <q-input
              v-model="form.task" outlined type="textarea" autogrow label="Auftrag" maxlength="40000" counter
              lazy-rules :rules="[requiredRule]" hint="Beschreibe das gewünschte Ergebnis eindeutig."
            />
            <q-select
              v-model="form.skillId" outlined emit-value map-options clearable label="Skill (optional)"
              :options="skillOptions" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
            />
            <q-toggle v-model="form.enabled" color="primary" label="Automation aktiv" />
            <q-banner rounded class="app-surface-tonal">
              <template #avatar><q-icon name="pause_circle_outline" color="primary" /></template>
              Eine deaktivierte Automation pausiert ihre Zeitpläne. Manuelle Läufe bleiben möglich.
            </q-banner>

            <q-banner v-if="sessionLoadFailed" rounded class="bg-negative text-white">
              Die Sessions konnten nicht vollständig geladen werden.
              <template #action><q-btn flat no-caps color="white" label="Erneut laden" :loading="sessionsLoading" @click="loadSessions" /></template>
            </q-banner>

            <template v-if="editingAutomation">
              <q-toggle v-model="form.copyRuntime" color="primary" label="Laufzeit erneut aus Session übernehmen" />
              <q-select
                v-if="form.copyRuntime" v-model="form.sourceSessionId" outlined emit-value map-options clearable
                label="Quell-Session" :options="sessionOptions" :rules="[requiredRule]"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
              />
            </template>
            <q-select
              v-else v-model="form.sourceSessionId" outlined emit-value map-options clearable
              label="Laufzeit aus Session übernehmen (optional)" :options="sessionOptions"
              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
              hint="Übernimmt Modell, Memory, Websuche und Arbeitsbereich."
            />

            <q-select
              v-model="form.outputSessionId" outlined emit-value map-options clearable label="Ausgabe-Session (optional)"
              :options="sessionOptions" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
              hint="Ergebnisse werden zusätzlich in diese Session geschrieben."
              @update:model-value="clearOutputRequirement"
            />
            <q-toggle
              v-if="form.outputSessionId" v-model="form.outputSessionRequired" color="primary"
              label="Ausgabe-Session als Pflichtbindung schützen"
            />

            <div>
              <div class="row items-center justify-between q-mb-sm">
                <div>
                  <div class="text-subtitle1 text-weight-bold">Zeitpläne</div>
                  <div class="text-caption app-muted">Wiederkehrend per Cron oder einmalig zu einem festen Zeitpunkt.</div>
                </div>
                <q-btn outline no-caps color="primary" icon="add" label="Zeitplan hinzufügen" :disable="form.schedules.length >= 50" @click="addSchedule" />
              </div>
              <q-card v-for="(schedule, index) in form.schedules" :key="index" flat class="app-surface-tonal q-mb-sm">
                <q-card-section class="row q-col-gutter-sm items-start">
                  <div class="col-12 col-md-3">
                    <q-select
                      v-model="schedule.kind" outlined dense emit-value map-options label="Ausführung"
                      :options="scheduleKindOptions" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                    />
                  </div>
                  <div v-if="schedule.kind === 'cron'" class="col-12 col-md-4">
                    <q-input
                      v-model="schedule.cron" outlined dense :label="`Cron ${index + 1}`"
                      maxlength="255" lazy-rules :rules="[requiredRule, cronRule]" hint="Minute Stunde Tag Monat Wochentag"
                    />
                  </div>
                  <div v-if="schedule.kind === 'cron'" class="col-12 col-md-4">
                    <q-input
                      v-model="schedule.timezone" outlined dense label="IANA-Zeitzone"
                      maxlength="100" lazy-rules :rules="[requiredRule]" hint="Zum Beispiel Europe/Berlin"
                    />
                  </div>
                  <div v-else class="col-12 col-md-8">
                    <q-input
                      v-model="schedule.runAt" outlined dense stack-label type="datetime-local"
                      label="Ausführungszeitpunkt" lazy-rules :rules="[requiredRule]"
                      hint="Lokale Uhrzeit dieses Geräts"
                    />
                  </div>
                  <div class="col-12 col-md-1 row justify-end">
                    <q-btn
                      flat round dense color="negative" icon="delete_outline"
                      :disable="form.schedules.length === 1" :aria-label="`Zeitplan ${index + 1} entfernen`"
                      @click="form.schedules.splice(index, 1)"
                    />
                  </div>
                  <div class="col-12"><q-toggle v-model="schedule.enabled" dense color="primary" label="Zeitplan aktiv" /></div>
                </q-card-section>
              </q-card>
              <q-toggle
                v-if="canArchiveAfterSuccess" v-model="form.archiveAfterSuccess" dense color="primary"
                label="Nach erfolgreicher Ausführung archivieren"
              />
            </div>
          </q-card-section>
          <q-separator />
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Abbrechen" :disable="saving" v-close-popup />
            <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="saving" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog v-model="detailDialogOpen" :maximized="$q.screen.lt.sm" aria-labelledby="automation-detail-title" @hide="closeDetail">
      <q-card class="automation-detail-dialog app-surface column no-wrap">
        <q-card-section class="row items-start no-wrap q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="history" />
          <div class="col">
            <div id="automation-detail-title" class="text-h6">{{ automations.detail?.name || 'Automation' }}</div>
            <div class="text-body2 app-muted">Definition, Ausführungskontext und Läufe</div>
          </div>
          <q-btn flat round dense icon="close" aria-label="Details schließen" v-close-popup />
        </q-card-section>
        <q-separator />
        <q-card-section v-if="automations.loadingDetail && !automations.detail" class="col flex flex-center">
          <q-spinner color="primary" size="36px" />
        </q-card-section>
        <q-card-section v-else-if="detailLoadFailed" class="col flex flex-center q-pa-md">
          <q-banner rounded class="bg-negative text-white">
            Die Automation oder der Lauf konnte nicht geladen werden.
            <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="openDetailFromQuery(route.query.automation, route.query.run)" /></template>
          </q-banner>
        </q-card-section>
        <q-card-section v-else-if="automations.detail" class="col scroll q-gutter-md">
          <q-card flat class="app-surface-tonal">
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

          <q-expansion-item icon="schedule" label="Zeitpläne" header-class="text-weight-medium" default-opened>
            <q-list bordered separator class="rounded-borders">
              <q-item v-for="schedule in automations.detail.schedules" :key="schedule.id || schedule.runAt || `${schedule.cron}-${schedule.timezone}`">
                <q-item-section>
                  <q-item-label v-if="schedule.runAt">Einmalig am {{ formatDate(schedule.runAt) }}</q-item-label>
                  <q-item-label v-else><code>{{ schedule.cron }}</code> · {{ schedule.timezone }}</q-item-label>
                  <q-item-label caption>{{ schedule.runAt ? 'Ausführung' : 'Nächster Lauf' }}: {{ automations.detail.archivedAt ? 'Archiviert' : automations.detail.enabled && schedule.enabled ? formatDate(schedule.nextRunAt) || 'Nicht geplant' : automations.detail.enabled ? 'Inaktiv' : 'Pausiert' }}</q-item-label>
                </q-item-section>
                  <q-item-section side><q-chip dense :color="automations.detail.enabled && schedule.enabled && !automations.detail.archivedAt ? 'positive' : 'grey-6'" :text-color="statusTextColor(automations.detail.enabled && schedule.enabled && !automations.detail.archivedAt ? 'positive' : 'grey-6')">{{ automations.detail.archivedAt ? 'Archiviert' : automations.detail.enabled && schedule.enabled ? 'Aktiv' : automations.detail.enabled ? 'Inaktiv' : 'Pausiert' }}</q-chip></q-item-section>
              </q-item>
            </q-list>
          </q-expansion-item>

          <q-expansion-item icon="tune" label="Laufzeitkontext" header-class="text-weight-medium">
            <q-list bordered separator class="rounded-borders">
              <q-item v-for="item in runtimeItems" :key="item.label">
                <q-item-section><q-item-label caption>{{ item.label }}</q-item-label><q-item-label>{{ item.value }}</q-item-label></q-item-section>
              </q-item>
            </q-list>
          </q-expansion-item>

          <q-expansion-item icon="forum" :label="`Ausführungskontext (${automations.detail.executionHistory?.length || 0})`" header-class="text-weight-medium">
            <q-list v-if="automations.detail.executionHistory?.length" bordered separator class="rounded-borders">
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

          <div>
            <div class="text-subtitle1 text-weight-bold q-mb-sm">Laufhistorie</div>
            <q-card v-if="automations.selectedRun" flat class="app-surface-tonal q-mb-md">
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
                    <template v-if="snapshot.runAt">Einmalig</template><template v-else><code>{{ snapshot.cron }}</code> · {{ snapshot.timezone }}</template><span v-if="snapshot.scheduledFor"> · {{ formatDate(snapshot.scheduledFor) }}</span>
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
                    v-if="automations.selectedRun.delivery?.status === 'failed'" outline no-caps color="primary"
                    icon="refresh" label="Zustellung erneut versuchen" class="q-mt-sm"
                    :loading="retryingRunId === automations.selectedRun.id" @click="retryDelivery(automations.selectedRun)"
                  />
                </div>
                <div class="row q-col-gutter-md q-mt-md text-caption app-muted">
                  <div class="col-12 col-sm-4">Erstellt: {{ formatDate(automations.selectedRun.createdAt) }}</div>
                  <div class="col-12 col-sm-4">Gestartet: {{ formatDate(automations.selectedRun.startedAt) || 'Noch nicht' }}</div>
                  <div class="col-12 col-sm-4">Beendet: {{ formatDate(automations.selectedRun.completedAt) || 'Noch nicht' }}</div>
                </div>
              </q-card-section>
            </q-card>

            <q-list v-if="automations.runs.length" bordered separator class="rounded-borders">
              <q-item
                v-for="run in automations.runs" :key="run.id" clickable v-ripple
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
            <div class="row justify-center q-mt-md">
              <q-spinner v-if="automations.loadingRuns" color="primary" size="28px" />
              <q-btn v-else-if="automations.hasMore" outline no-caps color="primary" label="Weitere Läufe laden" @click="loadMoreRuns" />
            </div>
          </div>
        </q-card-section>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { apiErrorMessage } from 'src/utils/api-errors'
import { formatAbsoluteDateTime, formatDateTimeLocalInput, localDateTimeToIso } from 'src/utils/date-time'
import { useAutomationStore } from 'stores/automations'
import { useChatStore } from 'stores/chat'
import { useSkillStore } from 'stores/skills'

defineOptions({ name: 'AutomationSettingsPage' })

const ACTIVE_STATUSES = new Set(['queued', 'running', 'waiting_approval', 'waiting_input'])
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const automations = useAutomationStore()
const chat = useChatStore()
const skills = useSkillStore()
const loaded = ref(false)
const loadFailed = ref(false)
const showArchived = ref(false)
const formDialogOpen = ref(false)
const detailDialogOpen = ref(false)
const formRef = ref(null)
const editingAutomation = ref(null)
const saving = ref(false)
const runningId = ref(null)
const retryingRunId = ref(null)
const detailLoadFailed = ref(false)
const sessionLoadFailed = ref(false)
const sessionsLoading = ref(false)
let refreshTimer = null
let refreshPending = false
let detailRequestRevision = 0
const form = reactive({
  name: '', task: '', skillId: null, enabled: true, sourceSessionId: null, copyRuntime: false,
  outputSessionId: null, outputSessionRequired: false, archiveAfterSuccess: false, schedules: []
})

const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
const cronRule = (value) => String(value || '').trim().split(/\s+/).length === 5 || 'Cron muss genau fünf Felder enthalten.'
const scheduleKindOptions = [
  { label: 'Wiederkehrend', value: 'cron' },
  { label: 'Einmalig', value: 'once' }
]
const canArchiveAfterSuccess = computed(() => (
  form.schedules.length === 1 && form.schedules[0]?.kind === 'once'
))
const sessionOptions = computed(() => {
  const options = chat.sessionCatalog.map((session) => ({ label: session.title, value: session.id }))
  for (const id of [form.outputSessionId, automations.detail?.outputSessionId]) {
    if (id && !options.some((option) => option.value === id)) {
      options.push({ label: 'Session nicht verfügbar', value: id })
    }
  }
  return options
})
const skillOptions = computed(() => skills.items.map((skill) => ({ label: skill.name, value: skill.id })))
const runtimeItems = computed(() => {
  const runtime = automations.detail?.runtime || {}
  const labels = {
    full: 'Vollständig',
    read_only: 'Nur lesen',
    disabled: 'Deaktiviert',
    required: 'Erforderlich',
    available: 'Verfügbar',
    'read-only': 'Nur lesen',
    'read-write': 'Lesen und schreiben'
  }
  return [
    ['Modell', runtime.modelId],
    ['Memory', runtime.memoryMode],
    ['Websuche', runtime.webSearchPolicy],
    ['Computer', runtime.preferredExecutionAgentId],
    ['Arbeitsbereich', runtime.preferredRootId],
    ['Arbeitsordner', runtime.workingDirectory],
    ['Aktueller Ordner', runtime.currentDirectory],
    ['Zugriff', runtime.workspaceAccess]
  ].map(([label, value]) => ({ label, value: labels[value] || value || 'Nicht festgelegt' }))
})

function blankSchedule() {
  return { kind: 'cron', cron: '0 8 * * 1-5', timezone: 'Europe/Berlin', runAt: '', enabled: true }
}

async function loadAutomations() {
  loadFailed.value = false
  try {
    await automations.fetchAutomations({ archived: showArchived.value })
    loaded.value = true
  } catch {
    loadFailed.value = true
  }
}

function resetForm(automation = null) {
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
    schedules: automation?.schedules?.map(({ cron, timezone, runAt, enabled }) => ({
      kind: runAt ? 'once' : 'cron',
      cron: cron || '0 8 * * 1-5',
      timezone: timezone || 'Europe/Berlin',
      runAt: formatDateTimeLocalInput(runAt),
      enabled
    })) || [blankSchedule()]
  })
}

function openCreateDialog() {
  resetForm()
  formDialogOpen.value = true
}

function openEditDialog(automation) {
  resetForm(automation)
  formDialogOpen.value = true
}

function addSchedule() {
  form.schedules.push(blankSchedule())
}

function clearOutputRequirement(value) {
  if (!value) form.outputSessionRequired = false
}

async function saveAutomation() {
  if (!await formRef.value?.validate()) return
  saving.value = true
  const payload = {
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
      : { cron: schedule.cron.trim(), timezone: schedule.timezone.trim(), enabled: schedule.enabled })
  }
  try {
    if (editingAutomation.value) {
      await automations.updateAutomation(editingAutomation.value.id, {
        ...payload, expectedRevision: editingAutomation.value.revision
      })
      $q.notify({ type: 'positive', message: 'Automation gespeichert' })
    } else {
      await automations.createAutomation(payload)
      $q.notify({ type: 'positive', message: 'Automation erstellt' })
    }
    formDialogOpen.value = false
  } catch {
    // The central Axios interceptor displays the error; keep the dialog open.
  } finally {
    saving.value = false
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
    await showDetail(automation.id, run.id)
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    runningId.value = null
  }
}

async function showDetail(automationId, runId = null) {
  await router.push({
    name: 'settings-automations',
    query: { automation: automationId, ...(runId ? { run: runId } : {}) }
  })
}

async function openDetailFromQuery(automationId, runId) {
  if (!automationId) return
  const requestRevision = ++detailRequestRevision
  detailLoadFailed.value = false
  automations.clearDetail()
  detailDialogOpen.value = true
  try {
    await automations.fetchAutomation(automationId, { suppressErrorToast: true })
    await Promise.all([
      automations.fetchRuns(automationId, 1, { suppressErrorToast: true }),
      ...(runId ? [automations.fetchRun(automationId, runId)] : [])
    ])
    if (requestRevision === detailRequestRevision) detailLoadFailed.value = false
  } catch {
    if (requestRevision === detailRequestRevision) detailLoadFailed.value = true
  }
}

async function closeDetail() {
  detailRequestRevision += 1
  automations.clearDetail()
  if (route.name === 'settings-automations' && (route.query.automation || route.query.run)) {
    await router.replace({ name: 'settings-automations' })
  }
}

async function selectRun(run) {
  await router.replace({
    name: 'settings-automations', query: { automation: run.automationId, run: run.id }
  })
}

async function loadMoreRuns() {
  if (automations.detail) await automations.fetchRuns(automations.detail.id, automations.page + 1)
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

function activeScheduleCount(automation) {
  return automation.enabled ? automation.schedules.filter((schedule) => schedule.enabled).length : 0
}

function statusTextColor(color) {
  return ['positive', 'warning'].includes(color) ? 'dark' : 'white'
}

function sessionTitle(id) {
  return chat.sessionCatalog.find((session) => session.id === id)?.title
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

function formatDate(value) {
  return formatAbsoluteDateTime(value)
}

function runError(run) {
  return apiErrorMessage(run.error, 'Der Automationslauf ist fehlgeschlagen.')
}

watch(
  () => [route.query.automation, route.query.run],
  ([automationId, runId], previous) => {
    if (automationId && (automationId !== previous?.[0] || runId !== previous?.[1])) {
      void openDetailFromQuery(automationId, runId)
    }
  },
  { immediate: true }
)

watch(showArchived, () => {
  loaded.value = false
  void loadAutomations()
})

onMounted(() => {
  void loadAutomations()
  void skills.fetchSkills().catch(() => {})
  void loadSessions()
  refreshTimer = window.setInterval(refreshAutomationState, 5000)
})

async function refreshAutomationState() {
  if (refreshPending) return
  refreshPending = true
  try {
    await automations.fetchAutomations({ archived: showArchived.value, suppressErrorToast: true })
    loaded.value = true
    loadFailed.value = false
    const automationId = route.query.automation
    if (automationId && detailDialogOpen.value) {
      await Promise.all([
        automations.fetchAutomation(automationId, { suppressErrorToast: true }),
        automations.fetchRuns(automationId, 1, { suppressErrorToast: true, preserveLoaded: true }),
        ...(route.query.run
          ? [automations.fetchRun(automationId, route.query.run, { suppressErrorToast: true })]
          : [])
      ])
      detailLoadFailed.value = false
    }
  } catch {
    // Background refresh keeps the last canonical state and retries later.
  } finally {
    refreshPending = false
  }
}

onBeforeUnmount(() => window.clearInterval(refreshTimer))
</script>

<style scoped>
.automation-dialog,
.automation-detail-dialog {
  width: min(900px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
}

@media (max-width: 599px) {
  .automation-dialog,
  .automation-detail-dialog {
    width: 100%;
    max-height: 100dvh;
  }
}
</style>
