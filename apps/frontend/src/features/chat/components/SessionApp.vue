<template>
  <section class="column full-height no-wrap" aria-label="Session">
    <q-scroll-area class="col">
      <section class="q-pa-md" aria-labelledby="session-work-context-title">
        <div id="session-work-context-title" class="text-subtitle1 text-weight-bold">Arbeitskontext</div>
        <div class="text-body2 q-mt-sm">Computer: {{ computerName }}</div>
        <div class="text-caption app-muted q-mt-sm">Arbeitsverzeichnis</div>
        <pre class="tool-call__details q-mt-xs">{{ chat.activeContext?.currentDirectory || 'Kein Arbeitsverzeichnis gewählt' }}</pre>
        <div class="text-caption app-muted">Die Ordnerwahl dient der Navigation. Datei- und Shell-Aktionen werden separat geprüft.</div>
      </section>
      <q-separator />
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
        <section class="q-pa-md" aria-labelledby="session-rules-title">
          <div class="row items-center no-wrap">
            <div id="session-rules-title" class="text-subtitle1 text-weight-bold">Wirksame Regeln</div>
            <q-space />
            <q-btn flat round dense icon="refresh" aria-label="Regeln aktualisieren" :loading="approvals.rulesLoading" @click="loadRules" />
          </div>
          <q-skeleton v-if="approvals.rulesLoading" type="text" class="q-mt-sm" aria-label="Regeln werden geladen" />
          <q-banner v-else-if="approvals.rulesLoadFailed" rounded class="app-surface-tonal q-mt-sm">
            Regeln konnten nicht geladen werden.
            <template #action><q-btn flat no-caps color="primary" label="Erneut laden" @click="loadRules" /></template>
          </q-banner>
          <template v-else-if="rules">
            <div class="text-caption app-muted q-my-sm">
              {{ rules.trustLevel === 'private' ? 'Private Modelle' : rules.trustLevel === 'public' ? 'Öffentliche Modelle' : 'Modellvertrauen nicht angegeben' }}
              · Profil und Modellvertrauen des aktiven Laufs, sonst der aktuellen Session.
              Automatisch gilt nur innerhalb der aktuellen technischen, Computer- und Ressourcengrenzen.
            </div>
            <q-expansion-item v-for="group in ruleGroups" :key="group.id" :label="group.label" :icon="group.icon" class="app-surface q-mb-sm">
              <q-list dense separator>
                <q-item v-for="action in group.actions" :key="action.key">
                  <q-item-section>
                    <q-item-label>{{ action.label }}</q-item-label>
                    <q-item-label caption>{{ action.capability }}</q-item-label>
                    <q-item-label>{{ action.integration ? 'Je nach Integration und Ressource' : policyLabel(action.policy) }}</q-item-label>
                    <q-item-label v-if="action.available === false" caption class="text-warning">Technisch nicht verfügbar</q-item-label>
                  </q-item-section>
                </q-item>
              </q-list>
            </q-expansion-item>
            <q-expansion-item label="Integrations- und Ressourcenregeln" icon="extension" class="app-surface q-mb-sm">
              <div class="text-caption app-muted q-pa-md">
                Integrationsrechte gelten nur für die genannte Verbindung; Ressourcenregeln haben dort Vorrang.
                Ohne gemeldete Abweichung gilt die integrationsspezifische Vorgabe, nicht pauschal der Tool-Katalogwert.
              </div>
              <q-list v-for="scope in scopedRuleGroups" :key="scope.id" dense separator>
                <q-item-label header>{{ integrationName(scope.id) }}</q-item-label>
                <q-item v-for="(action, index) in scope.actions" :key="index">
                  <q-item-section>
                    <q-item-label>{{ action.capability }} · {{ policyLabel(action.policy) }}</q-item-label>
                    <q-item-label v-if="!action.resource" caption>Integrationsregel</q-item-label>
                    <template v-else>
                      <q-item-label caption>Ressourcenregel</q-item-label>
                      <pre class="tool-call__details">{{ action.resource }}</pre>
                    </template>
                  </q-item-section>
                </q-item>
              </q-list>
              <div v-if="!scopedRuleGroups.length" class="text-caption app-muted q-pa-md">Keine expliziten Integrations- oder Ressourcenabweichungen gemeldet.</div>
            </q-expansion-item>
            <q-expansion-item label="Shell-Präfixe" icon="terminal" class="app-surface">
              <q-list dense>
                <q-item v-for="access in ['read', 'write']" :key="access">
                  <q-item-section>
                    <q-item-label>{{ access === 'read' ? 'Lesen' : 'Schreiben' }}</q-item-label>
                    <pre class="tool-call__details">{{ rules.shellPrefixes?.[access] ? rules.shellPrefixes[access].map((prefix) => prefix.join(' ')).join('\n') || 'Keine' : 'Nicht angegeben' }}</pre>
                  </q-item-section>
                </q-item>
              </q-list>
            </q-expansion-item>
          </template>
          <q-btn
            v-if="toolRulesRoute"
            flat no-caps color="primary" icon="security" label="Tool-Regeln öffnen"
            :to="toolRulesRoute" class="q-mt-sm"
          />
        </section>
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
          <div class="row items-center no-wrap q-mb-md">
            <div id="session-permissions-title" class="text-subtitle1 text-weight-bold">Explizite Freigaben</div>
            <q-space />
            <q-btn
              flat
              round
              dense
              icon="refresh"
              aria-label="Freigaben aktualisieren"
              :loading="approvals.grantsLoading"
              :disable="Boolean(revokingId)"
              @click="loadGrants"
            >
              <q-tooltip>Aktualisieren</q-tooltip>
            </q-btn>
            <q-btn
              v-if="sessionGrants.length && !approvals.grantsLoading && !approvals.grantsLoadFailed"
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
          <q-card v-if="approvals.grantsLoading" flat class="app-surface">
            <q-card-section class="row items-center no-wrap q-gutter-md">
              <q-skeleton type="QAvatar" />
              <div class="col"><q-skeleton type="text" width="35%" /><q-skeleton type="text" width="75%" /></div>
            </q-card-section>
          </q-card>
          <q-card v-else-if="approvals.grantsLoadFailed" flat class="app-surface">
            <q-card-section class="column items-center text-center q-py-lg">
              <q-icon name="cloud_off" size="32px" class="app-muted" />
              <div class="q-mt-sm">Freigaben konnten nicht geladen werden.</div>
              <q-btn class="q-mt-sm" flat no-caps color="primary" label="Erneut laden" @click="loadGrants" />
            </q-card-section>
          </q-card>
          <q-card v-else flat class="app-surface">
            <q-list separator>
              <q-item v-for="grant in visibleGrants" :key="grant.id">
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
                  <q-item-label caption>{{ grant.runId ? 'Bis zum Ende des ausführenden Laufs' : 'Für diese Session · bis zum Widerruf' }}</q-item-label>
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
              <q-item v-if="!visibleGrants.length">
                <q-item-section class="app-muted">Keine expliziten Freigaben vorhanden. Automatische Regeln gelten weiterhin.</q-item-section>
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
import { useAuthStore } from 'stores/auth'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { agentDisplayName } from 'src/features/computers/display'

defineOptions({ name: 'SessionApp' })

const approvals = useApprovalStore()
const chat = useChatStore()
const auth = useAuthStore()
const profiles = useAgentProfileStore()
const computers = useExecutionAgentStore()
const $q = useQuasar()
const { confirm } = useConfirm()
const revokingId = ref(null)
const visibleGrants = computed(() => approvals.grantsForSession(chat.current?.id))
const sessionGrants = computed(() => visibleGrants.value.filter((grant) => !grant.runId))
const rules = computed(() => approvals.rulesSessionId === chat.current?.id ? approvals.rules : null)
const policyLabel = (policy) => ({ auto: 'Automatisch', ask: 'Nachfragen', disabled: 'Deaktiviert' })[policy] || 'Nicht angegeben'
const ruleGroups = computed(() => {
  const seen = new Set()
  return (rules.value?.groups || []).map((group) => ({
    ...group,
    actions: [
      ...(group.actions || []),
      ...(group.tools || []).flatMap((tool) => (tool.actions || []).map((action) => ({
        ...action,
        integration: tool.source?.type === 'integration',
        available: tool.available !== false && action.available !== false
      })))
    ].map((action) => ({
      ...action, key: JSON.stringify([action.capability, action.integration === true, action.policy, action.available !== false])
    })).filter((action) => {
      if (seen.has(action.key)) return false
      seen.add(action.key)
      return true
    })
  })).filter((group) => group.actions.length)
})
const scopedRuleGroups = computed(() => {
  const { integrationPolicies = {}, resourcePolicies = {} } = rules.value?.scopedPolicies || {}
  return [...new Set([...Object.keys(integrationPolicies), ...Object.keys(resourcePolicies)])].map((id) => ({
    id,
    actions: [
      ...Object.entries(integrationPolicies[id] || {}).map(([capability, policy]) => ({ capability, policy })),
      ...Object.entries(resourcePolicies[id] || {}).flatMap(([resource, policies]) => (
        Object.entries(policies).map(([capability, policy]) => ({ resource, capability, policy }))
      ))
    ]
  })).filter((scope) => scope.actions.length)
})
const integrationName = (id) => profiles.integrations.find((item) => item.id === id)?.name || `Integration ${id}`
const computerName = computed(() => {
  const id = chat.activeContext?.preferredExecutionAgentId
  return id ? agentDisplayName(computers.byId(id)) || id : 'Kein Computer gewählt'
})
const toolRulesRoute = computed(() => {
  const profile = profiles.byId(chat.activeContext?.agentProfileId || profiles.defaultAgentProfileId)
  if (!profile) return null
  if (profile.scope === 'personal' && (auth.isAdmin || profiles.personalToolPermissionsEnabled && profiles.personalAgentsEnabled)) {
    return { name: 'settings-tools', query: { profileId: profile.id } }
  }
  return auth.isAdmin ? { name: 'admin-tools', query: { profileId: profile.baseProfileId || profile.id } } : null
})
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
  await approvals.fetchGrants({ suppressErrorToast: true }).catch(() => {})
}

async function loadRules() {
  await approvals.fetchRules(chat.current?.id, { suppressErrorToast: true }).catch(() => {})
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
  () => [chat.current?.id, chat.current?.agentProfileId, chat.current?.modelId, chat.current?.sessionProfile,
    chat.current?.preferredExecutionAgentId, chat.current?.currentDirectory, chat.runId, chat.current?.activeRun?.id],
  ([sessionId]) => {
    if (sessionId) void loadGrants()
    void loadRules()
  },
  { immediate: true }
)
</script>
