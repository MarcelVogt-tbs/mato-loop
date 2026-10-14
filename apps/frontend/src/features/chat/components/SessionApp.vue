<template>
  <section class="column full-height no-wrap" aria-label="Session">
    <q-scroll-area class="col">
      <SessionSettingsFields
        :session="chat.activeContext"
        :disabled="settingsDisabled"
        :fields="['template', 'messageSize', 'agent', 'computer', 'memory', 'webSearch', 'model', 'reasoning']"
        grouped
        show-warning
        class="q-pa-md"
        @update="chat.updateActive"
      />
      <template v-if="chat.current">
        <q-separator />
        <q-item class="q-px-md q-py-sm">
          <q-item-section avatar><q-icon :name="defaultChannel.icon" color="primary" /></q-item-section>
          <q-item-section>
            <q-item-label>Standardkanal</q-item-label>
            <q-item-label caption>{{ defaultChannel.label }}</q-item-label>
          </q-item-section>
        </q-item>
        <q-separator />
        <section class="q-pa-md" aria-labelledby="session-permissions-title">
          <div id="session-permissions-title" class="text-subtitle1 text-weight-bold q-mb-sm">Freigaben</div>
          <div class="row items-center no-wrap q-mb-md">
            <div class="text-subtitle2">Session-Freigaben</div>
            <q-space />
            <q-btn
              flat
              round
              dense
              icon="refresh"
              aria-label="Session-Freigaben aktualisieren"
              :loading="approvals.grantsLoading"
              :disable="Boolean(revokingId)"
              @click="loadGrants"
            >
              <q-tooltip>Aktualisieren</q-tooltip>
            </q-btn>
            <q-btn
              v-if="sessionGrants.length && loadedGrantContext === grantContext && !approvals.grantsLoadFailed"
              flat
              round
              dense
              color="negative"
              icon="delete_sweep"
              aria-label="Alle Session-Freigaben widerrufen"
              :loading="revokingId === 'all'"
              :disable="Boolean(revokingId)"
              @click="revokeAll"
            >
              <q-tooltip>Alle Session-Freigaben widerrufen</q-tooltip>
            </q-btn>
          </div>
          <q-card v-if="approvals.grantsLoading && loadedGrantContext !== grantContext" flat class="app-surface">
            <q-card-section class="row items-center no-wrap q-gutter-md">
              <q-skeleton type="QAvatar" />
              <div class="col"><q-skeleton type="text" width="35%" /><q-skeleton type="text" width="75%" /></div>
            </q-card-section>
          </q-card>
          <q-card v-else-if="approvals.grantsLoadFailed" flat class="app-surface">
            <q-card-section class="column items-center text-center q-py-lg">
              <q-icon name="cloud_off" size="32px" class="app-muted" />
              <div class="q-mt-sm">Session-Freigaben konnten nicht geladen werden.</div>
              <q-btn class="q-mt-sm" flat no-caps color="primary" label="Erneut laden" @click="loadGrants" />
            </q-card-section>
          </q-card>
          <q-card v-else flat class="app-surface">
            <q-list separator>
              <q-item v-for="grant in sessionGrants" :key="grant.id">
                <q-item-section avatar>
                  <q-icon :name="grantDisplay(grant).icon" :color="grant.applicable === false ? 'grey' : 'primary'" />
                </q-item-section>
                <q-item-section>
                  <q-item-label>{{ grantDisplay(grant).label }}</q-item-label>
                  <q-item-label v-if="grant.command" caption>Arbeitsverzeichnis</q-item-label>
                  <pre class="tool-call__details q-my-xs">{{ grantDisplay(grant).value }}</pre>
                  <q-item-label v-if="grant.executionAgentName || grant.executionAgentId" caption>
                    Computer: {{ grant.executionAgentName || grant.executionAgentId }}
                  </q-item-label>
                  <template v-if="grant.command">
                    <q-item-label caption>Exakter Befehl · gleiche Umgebung und Konfiguration</q-item-label>
                    <pre class="tool-call__details q-my-xs">{{ grant.command || 'Befehl nicht verfügbar' }}</pre>
                  </template>
                  <q-item-label v-if="grant.elevated || grant.resourceType === 'administrator_path'" caption>Mit Administratorrechten</q-item-label>
                  <q-item-label v-else-if="grant.command" caption>Ohne Administratorrechte</q-item-label>
                  <q-item-label caption>Für diese Session · bis zum Widerruf</q-item-label>
                  <q-item-label v-if="grant.applicable === false" caption class="text-warning">
                    Inaktiv · passt nicht zur aktuellen Bindung oder wird nicht mehr unterstützt
                  </q-item-label>
                  <q-item-label v-else-if="grant.applicable === true" caption>Bindung passt · Regeln und Grenzen werden bei der Aktion geprüft</q-item-label>
                  <q-item-label v-if="grant.createdAt" caption>{{ formatRelativeDateTime(grant.createdAt) }}</q-item-label>
                </q-item-section>
                <q-item-section side>
                  <q-btn
                    flat
                    round
                    dense
                    icon="delete_outline"
                    color="negative"
                    :loading="revokingId === grant.id"
                    :disable="Boolean(revokingId)"
                    :aria-label="`${grantDisplay(grant).label} widerrufen`"
                    @click="revoke(grant)"
                  >
                    <q-tooltip>Freigabe widerrufen</q-tooltip>
                  </q-btn>
                </q-item-section>
              </q-item>
              <q-item v-if="!sessionGrants.length">
                <q-item-section class="app-muted">Keine Session-Freigaben vorhanden.</q-item-section>
              </q-item>
            </q-list>
          </q-card>
        </section>
      </template>
    </q-scroll-area>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import SessionSettingsFields from 'src/features/chat/components/SessionSettingsFields.vue'
import { useConfirm } from 'src/composables/confirm'
import { permissionGrantDisplay } from 'src/utils/approval-display'
import { formatRelativeDateTime } from 'src/utils/date-time'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'

defineOptions({ name: 'SessionApp' })

const approvals = useApprovalStore()
const chat = useChatStore()
const $q = useQuasar()
const { confirm } = useConfirm()
const revokingId = ref(null)
const sessionGrants = computed(() => approvals.grantsForSession(chat.current?.id))
const grantContext = computed(() => JSON.stringify([
  chat.current?.id, chat.current?.agentProfileId, chat.current?.modelId, chat.current?.sessionProfile,
  chat.current?.preferredExecutionAgentId, chat.current?.currentDirectory, chat.runId, chat.current?.activeRun?.id
]))
const loadedGrantContext = ref(null)
const defaultChannel = computed(() => {
  const channel = chat.current?.defaultChannel
  if (channel?.type === 'endpoint' && channel.provider === 'telegram') {
    return { icon: 'send', label: 'Telegram' }
  }
  return { icon: 'devices', label: 'MatoLoop-App' }
})
const settingsDisabled = computed(() => Boolean(
  chat.running
  || chat.runId
  || chat.current?.activeRun
  || chat.sessionSettingsPending > 0
  || (chat.current && approvals.forSession(chat.current.id).length)
))
const grantDisplay = (grant) => permissionGrantDisplay(grant, chat.cachedSessions)

async function loadGrants() {
  if (!chat.current?.id) return
  const context = grantContext.value
  try {
    await approvals.fetchGrants({ suppressErrorToast: true })
    if (context === grantContext.value && !approvals.grantsLoadFailed) loadedGrantContext.value = context
  } catch { /* The retry state is displayed inline. */ }
}

async function revoke(grant) {
  const sessionId = chat.current?.id
  if (!sessionId) return
  const display = grantDisplay(grant)
  const accepted = await confirm(
    `Soll die Freigabe „${display.label}“ für ${[display.value, grant.executionAgentName || grant.executionAgentId, grant.command].filter(Boolean).join(' · ')} widerrufen werden?`,
    'Freigabe widerrufen',
    { okLabel: 'Widerrufen', okColor: 'negative' }
  )
  if (!accepted) return
  revokingId.value = grant.id
  try {
    await approvals.revoke(sessionId, grant.id)
    $q.notify({ type: 'positive', message: 'Freigabe widerrufen' })
  } finally {
    revokingId.value = null
  }
}

async function revokeAll() {
  const sessionId = chat.current?.id
  if (!sessionId || !sessionGrants.value.length) return
  const accepted = await confirm(
    `Sollen alle ${sessionGrants.value.length} Session-Freigaben widerrufen werden? Lauf-Freigaben und automatische Regeln gelten weiterhin.`,
    'Alle Session-Freigaben widerrufen',
    { okLabel: 'Alle widerrufen', okColor: 'negative' }
  )
  if (!accepted) return
  revokingId.value = 'all'
  try {
    await approvals.revokeAll(sessionId)
    $q.notify({ type: 'positive', message: 'Session-Freigaben widerrufen' })
  } finally {
    revokingId.value = null
  }
}

watch(
  grantContext,
  () => {
    loadedGrantContext.value = null
    void loadGrants()
  },
  { immediate: true }
)
</script>
