<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Ausführungsagenten</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Lokale Arbeitsbereiche und Ausführungsfunktionen mit MatoLoop verbinden.
        </p>
      </div>
      <q-btn color="primary" unelevated no-caps icon="add" label="Agent hinzufügen" @click="openCreate" />
    </header>

    <q-banner v-if="agents.loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Ausführungsagenten konnten nicht geladen werden.
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
            <q-item-label>Noch kein Ausführungsagent eingerichtet</q-item-label>
            <q-item-label caption>Füge einen Agent hinzu und verbinde ihn mit dem einmalig angezeigten Token.</q-item-label>
          </q-item-section>
        </q-item>

        <q-item v-for="agent in agents.items" :key="agent.id" class="execution-agent-item q-py-md">
          <q-item-section avatar top>
            <q-avatar :color="agent.connected ? 'positive' : 'grey-7'" text-color="white" icon="computer" />
          </q-item-section>
          <q-item-section>
            <div class="row items-center q-gutter-sm">
              <q-item-label class="text-weight-medium">{{ agent.name }}</q-item-label>
              <q-chip dense :color="agent.connected ? 'positive' : 'grey-7'" text-color="white">
                {{ agent.connected ? 'Online' : 'Offline' }}
              </q-chip>
            </div>
            <q-item-label caption>
              {{ agent.platform || 'Plattform unbekannt' }} · Protokoll {{ agent.protocolVersion }} · Revision {{ agent.revision }}
            </q-item-label>
            <q-item-label v-if="agent.lastSeenAt" caption>
              Zuletzt gesehen: {{ formatAbsoluteDateTime(agent.lastSeenAt) }}
            </q-item-label>

            <div class="q-mt-sm">
              <div class="text-caption app-muted">Arbeitsbereiche</div>
              <div v-if="agent.roots?.length" class="row q-gutter-xs q-mt-xs">
                <q-chip v-for="root in agent.roots" :key="root.id" dense outline icon="folder">
                  {{ root.label }}
                </q-chip>
              </div>
              <div v-else class="text-caption">Keine Arbeitsbereiche gemeldet.</div>
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
              <q-btn flat round dense icon="edit" :aria-label="`${agent.name} umbenennen`" @click="openRename(agent)">
                <q-tooltip>Umbenennen</q-tooltip>
              </q-btn>
              <q-btn
                flat
                round
                dense
                color="negative"
                icon="link_off"
                :aria-label="`${agent.name} widerrufen`"
                @click="revoke(agent)"
              >
                <q-tooltip>Zugriff widerrufen</q-tooltip>
              </q-btn>
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <q-inner-loading :showing="agents.loading" />
    </q-card>

    <q-dialog v-model="nameDialogOpen" :persistent="saving">
      <q-card class="execution-agent-dialog">
        <q-form @submit.prevent="saveName">
          <q-card-section>
            <div class="text-h6">{{ selectedAgent ? 'Agent umbenennen' : 'Agent hinzufügen' }}</div>
          </q-card-section>
          <q-card-section class="q-pt-none">
            <q-input
              v-model="name"
              autofocus
              outlined
              label="Name"
              maxlength="120"
              lazy-rules
              :rules="[requiredRule]"
            />
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md q-pt-none">
            <q-btn v-close-popup flat no-caps label="Abbrechen" :disable="saving" />
            <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="saving" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>

    <q-dialog v-model="tokenDialogOpen" persistent>
      <q-card class="execution-agent-dialog">
        <q-card-section>
          <div class="text-h6">Agent verbinden</div>
          <q-banner rounded class="app-surface-tonal q-mt-md">
            Das Token wird nur jetzt angezeigt. Bewahre es sicher auf und teile es nicht.
          </q-banner>
        </q-card-section>
        <q-card-section class="q-pt-none q-gutter-y-md">
          <q-input :model-value="createdAgent?.agent?.id" readonly outlined label="Agent-ID">
            <template #append>
              <q-btn flat round dense icon="content_copy" aria-label="Agent-ID kopieren" @click="copy(createdAgent?.agent?.id, 'Agent-ID')" />
            </template>
          </q-input>
          <q-input :model-value="createdAgent?.websocketUrl" readonly outlined label="WebSocket-Adresse">
            <template #append>
              <q-btn flat round dense icon="content_copy" aria-label="WebSocket-Adresse kopieren" @click="copy(createdAgent?.websocketUrl, 'WebSocket-Adresse')" />
            </template>
          </q-input>
          <q-input :model-value="createdAgent?.token" readonly outlined type="textarea" autogrow label="Einmaliges Token">
            <template #append>
              <q-btn flat round dense icon="content_copy" aria-label="Token kopieren" @click="copy(createdAgent?.token, 'Token')" />
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
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useConfirm } from 'src/composables/confirm'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { useExecutionAgentStore } from 'stores/execution-agents'

defineOptions({ name: 'ExecutionAgentSettingsPage' })

const operationLabels = {
  'cache.clear': 'Cache leeren',
  'cache.ensure': 'Datei im Cache bereitstellen',
  'cache.store': 'Datei im Cache speichern',
  'workspace.copy': 'Pfade kopieren',
  'workspace.delete': 'Pfade löschen',
  'workspace.export': 'Dateien exportieren',
  'workspace.import': 'Dateien importieren',
  'workspace.list': 'Ordner auflisten',
  'workspace.mkdir': 'Ordner erstellen',
  'workspace.move': 'Pfade verschieben',
  'workspace.read': 'Dateien lesen',
  'workspace.rename': 'Pfade umbenennen',
  'workspace.stat': 'Dateiinformationen lesen',
  'workspace.write': 'Dateien bearbeiten',
  'process.exec': 'Prozesse ausführen',
  'shell.run': 'Shell-Befehle ausführen'
}
const $q = useQuasar()
const { confirm } = useConfirm()
const agents = useExecutionAgentStore()
const nameDialogOpen = ref(false)
const tokenDialogOpen = ref(false)
const selectedAgent = ref(null)
const createdAgent = ref(null)
const name = ref('')
const saving = ref(false)
const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Name erforderlich.'
const operationLabel = (operation) => operationLabels[operation] || operation

function openCreate() {
  selectedAgent.value = null
  name.value = ''
  nameDialogOpen.value = true
}

function openRename(agent) {
  selectedAgent.value = agent
  name.value = agent.name
  nameDialogOpen.value = true
}

async function saveName() {
  const value = name.value.trim()
  if (!value || saving.value) return
  saving.value = true
  try {
    if (selectedAgent.value) {
      await agents.rename(selectedAgent.value.id, value)
      $q.notify({ type: 'positive', message: 'Agent umbenannt' })
    } else {
      createdAgent.value = await agents.create(value)
      tokenDialogOpen.value = true
    }
    nameDialogOpen.value = false
  } finally {
    saving.value = false
  }
}

async function revoke(agent) {
  const accepted = await confirm(
    `Zugriff für „${agent.name}“ widerrufen? Der Agent wird sofort getrennt.`,
    'Agent widerrufen',
    { okLabel: 'Widerrufen', okColor: 'negative' }
  )
  if (!accepted) return
  await agents.revoke(agent.id)
  $q.notify({ type: 'positive', message: 'Agent widerrufen' })
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
  createdAgent.value = null
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
})
onBeforeUnmount(() => agents.stopPolling())
</script>
