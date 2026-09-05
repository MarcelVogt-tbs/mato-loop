<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Computer</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Lokale Datei- und Shell-Funktionen mit MatoLoop verbinden.
        </p>
      </div>
      <q-btn color="primary" unelevated no-caps icon="add" label="Computer hinzufügen" @click="openCreate" />
    </header>

    <q-banner v-if="agents.loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Computer konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="primary" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <q-card flat class="app-surface execution-agent-list-card">
      <q-list separator>
        <q-item v-if="agents.loaded && !agents.items.length && !agents.loading" class="q-py-lg">
          <q-item-section avatar>
            <q-avatar color="grey-7" text-color="white" icon="computer" />
          </q-item-section>
          <q-item-section>
            <q-item-label>Noch kein Computer eingerichtet</q-item-label>
            <q-item-label caption>Füge einen Computer hinzu und verbinde ihn mit dem einmalig angezeigten Token.</q-item-label>
          </q-item-section>
        </q-item>

        <q-item v-for="agent in agents.items" :key="agent.id" class="execution-agent-item q-py-md">
          <q-item-section avatar top>
            <q-avatar :color="statusColor(agent)" :text-color="statusTextColor(agent)" :icon="statusIcon(agent)" />
          </q-item-section>
          <q-item-section>
            <div class="row items-center q-gutter-sm">
              <q-item-label class="text-weight-medium">{{ agent.alias }}</q-item-label>
              <q-chip dense :color="statusColor(agent)" :text-color="statusTextColor(agent)">
                {{ statusLabel(agent) }}
              </q-chip>
            </div>
            <q-item-label caption>
              {{ agent.computerName || 'Noch keine Verbindung' }}<template v-if="agent.platform"> · {{ agent.platform }}</template><template v-if="agent.agentVersion"> · Agent {{ agent.agentVersion }}</template>
            </q-item-label>
            <q-item-label v-if="agent.pairingState === 'pending' && agent.pairingTokenExpiresAt" caption>
              Kopplungstoken gültig bis: {{ formatExpiry(agent.pairingTokenExpiresAt) }}
            </q-item-label>
            <q-item-label v-if="agent.lastSeenAt" caption>
              Zuletzt gesehen: {{ formatAbsoluteDateTime(agent.lastSeenAt) }}
            </q-item-label>

            <div class="q-mt-sm">
              <div class="text-caption app-muted">Home-Verzeichnis</div>
              <div class="text-body2">{{ agent.homeDirectory || 'Noch nicht gemeldet' }}</div>
            </div>

            <div class="q-mt-sm">
              <div class="text-caption app-muted">Funktionen</div>
              <div v-if="agent.operations?.length" class="row q-gutter-xs q-mt-xs">
                <q-chip v-for="operation in agent.operations" :key="operation" dense>
                  {{ operationLabel(operation) }}
                </q-chip>
              </div>
              <div v-else class="text-caption">Keine Funktionen gemeldet.</div>
            </div>
          </q-item-section>
          <q-item-section side top class="execution-agent-item__actions">
            <div class="row items-center no-wrap q-gutter-xs">
              <q-btn flat round dense icon="edit" :aria-label="`${agent.alias} bearbeiten`" @click="openEdit(agent)">
                <q-tooltip>Bearbeiten</q-tooltip>
              </q-btn>
              <q-btn
                v-if="agent.pairingState === 'unpaired' || agent.pairingState === 'pending'"
                flat
                round
                dense
                color="primary"
                icon="key"
                :aria-label="`Neuen Kopplungstoken für ${agent.alias} erstellen`"
                @click="openPairingToken(agent)"
              >
                <q-tooltip>Neuen Kopplungstoken erstellen</q-tooltip>
              </q-btn>
              <q-btn
                v-if="agent.pairingState === 'paired'"
                flat
                round
                dense
                color="negative"
                icon="link_off"
                :aria-label="`${agent.alias} entkoppeln`"
                @click="unpair(agent)"
              >
                <q-tooltip>Computer entkoppeln</q-tooltip>
              </q-btn>
              <q-btn
                flat
                round
                dense
                color="negative"
                icon="delete_outline"
                :aria-label="`${agent.alias} entfernen`"
                @click="revoke(agent)"
              >
                <q-tooltip>Computer entfernen</q-tooltip>
              </q-btn>
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <q-inner-loading :showing="agents.loading" />
    </q-card>

    <ExecutionAgentConfigDialog
      v-model="configDialogOpen"
      :agent="selectedAgent"
      @created="showPairingToken"
    />

    <q-dialog v-model="pairingDialogOpen" :maximized="$q.screen.lt.sm" :persistent="saving">
      <q-card class="execution-agent-dialog">
        <q-form @submit.prevent="createPairingToken">
          <q-card-section>
            <div class="text-h6">Neuen Kopplungstoken erstellen</div>
            <q-banner v-if="pairingAgent?.pairingState === 'pending'" rounded class="app-surface-tonal q-mt-md">
              Der bisherige Kopplungstoken wird sofort ungültig.
            </q-banner>
          </q-card-section>
          <q-card-section class="q-pt-none">
            <q-select
              v-model="pairingDurationSeconds"
              outlined
              emit-value
              map-options
              :options="pairingDurationOptions"
              label="Gültigkeitsdauer des Kopplungstokens"
            />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md q-pt-none">
            <q-btn v-close-popup flat no-caps label="Abbrechen" :disable="saving" />
            <q-btn unelevated no-caps color="primary" label="Token erstellen" type="submit" :loading="saving" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog v-model="tokenDialogOpen" persistent :maximized="$q.screen.lt.sm" @hide="pairingResult = null">
      <q-card class="execution-agent-dialog">
        <q-card-section>
          <div class="text-h6">Kopplungstoken</div>
          <q-banner rounded class="app-surface-tonal q-mt-md">
            Dieser Kopplungstoken wird nur einmal angezeigt. Bewahre ihn sicher auf und teile ihn nicht.
          </q-banner>
        </q-card-section>
        <q-card-section class="q-pt-none q-gutter-y-md">
          <q-input :model-value="pairingResult?.agent?.id" readonly outlined label="Computer-ID">
            <template #append>
              <q-btn flat round dense icon="content_copy" aria-label="Computer-ID kopieren" @click="copy(pairingResult?.agent?.id, 'Computer-ID')" />
            </template>
          </q-input>
          <q-input :model-value="pairingResult?.websocketUrl" readonly outlined label="WebSocket-Adresse">
            <template #append>
              <q-btn flat round dense icon="content_copy" aria-label="WebSocket-Adresse kopieren" @click="copy(pairingResult?.websocketUrl, 'WebSocket-Adresse')" />
            </template>
          </q-input>
          <q-input :model-value="formatExpiry(pairingResult?.pairingTokenExpiresAt)" readonly outlined label="Gültig bis" />
          <q-input :model-value="pairingResult?.pairingToken" readonly outlined type="textarea" autogrow label="Kopplungstoken">
            <template #append>
              <q-btn flat round dense icon="content_copy" aria-label="Kopplungstoken kopieren" @click="copy(pairingResult?.pairingToken, 'Kopplungstoken')" />
            </template>
          </q-input>
        </q-card-section>
        <q-card-actions align="right" class="q-pa-md q-pt-none">
          <q-btn unelevated no-caps color="primary" label="Schließen" @click="closeTokenDialog" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { copyToClipboard, useQuasar } from 'quasar'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ExecutionAgentConfigDialog from 'components/ExecutionAgentConfigDialog.vue'
import { useConfirm } from 'src/composables/confirm'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { useExecutionAgentStore } from 'stores/execution-agents'

defineOptions({ name: 'ExecutionAgentSettingsPage' })

const operationLabels = {
  'cache.clear': 'Cache leeren',
  'cache.ensure': 'Datei im Cache bereitstellen',
  'cache.store': 'Datei im Cache speichern',
  'filesystem.resolve': 'Verzeichnis auflösen',
  'filesystem.copy': 'Pfade kopieren',
  'filesystem.delete': 'Pfade löschen',
  'filesystem.export': 'Dateien exportieren',
  'filesystem.import': 'Dateien importieren',
  'filesystem.list': 'Ordner auflisten',
  'filesystem.glob': 'Dateien suchen',
  'filesystem.grep': 'Dateiinhalte durchsuchen',
  'filesystem.mkdir': 'Ordner erstellen',
  'filesystem.move': 'Pfade verschieben',
  'filesystem.read': 'Dateien lesen',
  'filesystem.rename': 'Pfade umbenennen',
  'filesystem.stat': 'Dateiinformationen lesen',
  'filesystem.write': 'Dateien bearbeiten',
  'shell.run': 'Shell-Befehle ausführen',
  'shell.inspect': 'Shell-Konfiguration prüfen',
  'system.info': 'Systeminformationen lesen'
}
const pairingDurationChoices = [
  { label: '30 Minuten', value: 1800 },
  { label: '1 Stunde', value: 3600 },
  { label: '4 Stunden', value: 14_400 },
  { label: '12 Stunden', value: 43_200 },
  { label: '24 Stunden', value: 86_400 }
]
const expiryFormatter = new Intl.DateTimeFormat('de-DE', {
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short'
})
const $q = useQuasar()
const { confirm } = useConfirm()
const agents = useExecutionAgentStore()
const configDialogOpen = ref(false)
const pairingDialogOpen = ref(false)
const tokenDialogOpen = ref(false)
const selectedAgent = ref(null)
const pairingAgent = ref(null)
const pairingResult = ref(null)
const pairingDurationSeconds = ref(null)
const saving = ref(false)
const operationLabel = (operation) => operationLabels[operation] || operation
const pairingDurationOptions = computed(() => pairingDurationChoices.filter((option) => (
  option.value <= (agents.pairingPolicy?.maximumDurationSeconds || 0)
)))
function openCreate() {
  selectedAgent.value = null
  configDialogOpen.value = true
}

async function openPairingToken(agent) {
  try {
    const policy = await agents.fetchPairingPolicy()
    pairingDurationSeconds.value = policy.defaultDurationSeconds
  } catch {
    return
  }
  pairingAgent.value = agent
  pairingDialogOpen.value = true
}

function openEdit(agent) {
  selectedAgent.value = agent
  configDialogOpen.value = true
}

function showPairingToken(result) {
  pairingResult.value = result
  tokenDialogOpen.value = true
}

async function createPairingToken() {
  if (!pairingAgent.value || saving.value) return
  saving.value = true
  try {
    pairingResult.value = await agents.createPairingToken(pairingAgent.value.id, pairingDurationSeconds.value)
    pairingDialogOpen.value = false
    tokenDialogOpen.value = true
  } finally {
    saving.value = false
  }
}

async function unpair(agent) {
  const accepted = await confirm(
    `„${agent.alias}“ entkoppeln? Der Computer wird sofort getrennt und muss für eine neue Verbindung erneut gekoppelt werden.`,
    'Computer entkoppeln',
    { okLabel: 'Entkoppeln', okColor: 'negative' }
  )
  if (!accepted) return
  await agents.unpair(agent.id)
  $q.notify({ type: 'positive', message: 'Computer entkoppelt' })
}

async function revoke(agent) {
  const accepted = await confirm(
    `„${agent.alias}“ entfernen? Der Zugriff wird sofort widerrufen und der Computer aus der Liste entfernt.`,
    'Computer entfernen',
    { okLabel: 'Entfernen', okColor: 'negative' }
  )
  if (!accepted) return
  await agents.revoke(agent.id)
  $q.notify({ type: 'positive', message: 'Computer entfernt' })
}

async function copy(value, label) {
  if (!value) return
  try {
    await copyToClipboard(value)
    $q.notify({ type: 'positive', message: `${label} kopiert` })
  } catch {
    $q.notify({ type: 'negative', message: `${label} konnte nicht kopiert werden` })
  }
}

function closeTokenDialog() {
  tokenDialogOpen.value = false
  pairingResult.value = null
}

function formatExpiry(value) {
  const date = value ? new Date(value) : null
  return date && !Number.isNaN(date.getTime()) ? expiryFormatter.format(date) : ''
}

function statusLabel(agent) {
  if (agent.pairingState === 'unpaired') return 'Nicht gekoppelt'
  if (agent.pairingState === 'pending') return 'Kopplung ausstehend'
  return agent.connected ? 'Verbunden' : 'Offline'
}

function statusColor(agent) {
  if (agent.pairingState === 'pending') return 'warning'
  if (agent.pairingState === 'unpaired') return 'blue-grey-8'
  if (agent.pairingState === 'paired' && agent.connected) return 'green-9'
  return 'grey-8'
}

function statusTextColor(agent) {
  return agent.pairingState === 'pending' ? 'black' : 'white'
}

function statusIcon(agent) {
  if (agent.pairingState === 'unpaired') return 'link_off'
  if (agent.pairingState === 'pending') return 'hourglass_top'
  return agent.connected ? 'computer' : 'cloud_off'
}

async function load(config = null) {
  try {
    await agents.fetchAll(config)
  } catch {
    // Initial API errors use the central toast; polling keeps the inline state current.
  }
}

onMounted(() => {
  agents.startPolling()
  void agents.fetchPairingPolicy().catch(() => {})
})
onBeforeUnmount(() => agents.stopPolling())
</script>
