<template>
  <router-view v-if="showAccessPage" />
  <section v-else>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Integrationen</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Externe Dienste für zusätzliche Agent-Funktionen verwalten.
        </p>
      </div>
      <q-btn
        color="primary"
        unelevated
        no-caps
        icon="add"
        label="Integration hinzufügen"
        @click="openCreateDialog"
      />
    </header>

    <q-banner v-if="integrations.loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Integrationen konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="primary" label="Erneut laden" @click="reload" />
      </template>
    </q-banner>

    <q-card flat class="app-surface integration-list-card">
      <q-list separator>
        <q-item v-if="integrations.loaded && !configuredIntegrations.length && !integrations.loading" class="q-py-lg">
          <q-item-section avatar>
            <q-avatar color="grey-7" text-color="white" icon="extension" />
          </q-item-section>
          <q-item-section>
            <q-item-label>Noch keine Integration konfiguriert</q-item-label>
            <q-item-label caption>Füge einen externen Dienst wie SearXNG, CalDAV oder BookStack hinzu.</q-item-label>
          </q-item-section>
        </q-item>

        <q-item v-for="integration in configuredIntegrations" :key="integration.id" class="q-py-md">
          <q-item-section avatar top>
            <q-avatar
              color="primary"
              text-color="white"
              :icon="typeIcon(integration.integrationType)"
            />
          </q-item-section>
          <q-item-section>
            <div class="row items-center q-gutter-sm">
              <q-item-label class="text-weight-medium">{{ integration.name }}</q-item-label>
              <q-chip dense :color="integration.enabled ? 'positive' : 'grey-7'" text-color="white">
                {{ integration.enabled ? 'Aktiv' : 'Deaktiviert' }}
              </q-chip>
            </div>
            <q-item-label caption>
              {{ typeLabel(integration.integrationType) }} ·
              {{ providerLabel(integration.providerType) }}
            </q-item-label>
            <q-item-label v-if="integration.integrationType === 'git'" caption lines="1">
              Computer: {{ executionAgentName(integration) }} ·
              Netzwerkzugriff {{ gitAllowNetwork(integration) ? 'erlaubt' : 'gesperrt' }}
            </q-item-label>
            <q-item-label v-else caption lines="1">
              {{ integration.config.baseUrl }}{{ integration.config.username ? ` · ${integration.config.username}` : '' }}
            </q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row items-center no-wrap q-gutter-xs">
              <q-btn
                v-if="integration.supportsResourcePolicies"
                flat
                dense
                no-caps
                :round="$q.screen.lt.md"
                icon="policy"
                :label="$q.screen.gt.sm ? 'Zugriff' : undefined"
                aria-label="Zugriff verwalten"
                :to="{ name: 'settings-integration-access', params: { integrationId: integration.id } }"
              />
              <q-btn
                v-if="integration.integrationType !== 'git'"
                flat
                dense
                no-caps
                :round="$q.screen.lt.md"
                icon="network_check"
                :label="$q.screen.gt.sm ? 'Testen' : undefined"
                aria-label="Verbindung testen"
                :loading="testingId === integration.id"
                @click="testIntegration(integration)"
              />
              <q-btn
                flat
                dense
                no-caps
                :round="$q.screen.lt.md"
                icon="edit"
                :label="$q.screen.gt.sm ? 'Bearbeiten' : undefined"
                aria-label="Integration bearbeiten"
                @click="openEditDialog(integration)"
              />
              <q-btn
                flat
                round
                dense
                color="negative"
                icon="delete"
                :aria-label="`${integration.name} löschen`"
                @click="removeIntegration(integration)"
              />
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <q-inner-loading :showing="integrations.loading" />
    </q-card>

    <IntegrationConfigDialog v-model="dialogOpen" :integration="selectedIntegration" />
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import IntegrationConfigDialog from 'components/IntegrationConfigDialog.vue'
import { useConfirm } from 'src/composables/confirm'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'

defineOptions({ name: 'IntegrationSettingsPage' })

const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const executionAgents = useExecutionAgentStore()
const dialogOpen = ref(false)
const selectedIntegration = ref(null)
const testingId = ref(null)
const showAccessPage = computed(() => route.name === 'settings-integration-access')
const searchProviders = computed(() => integrations.search.providers)
const calendarAccounts = computed(() => integrations.calendar.accounts)
const knowledgeBases = computed(() => integrations.knowledge.bases)
const gitInstances = computed(() => integrations.git.instances)
const configuredIntegrations = computed(
  () => [...searchProviders.value, ...calendarAccounts.value, ...knowledgeBases.value, ...gitInstances.value]
)

function openCreateDialog() {
  selectedIntegration.value = null
  dialogOpen.value = true
}

function openEditDialog(integration) {
  selectedIntegration.value = integration
  dialogOpen.value = true
}

function providerLabel(providerType) {
  return { searxng: 'SearXNG', caldav: 'CalDAV', bookstack: 'BookStack', git: 'Git' }[providerType] || providerType
}

function typeLabel(integrationType) {
  return {
    calendar: 'Kalender & Aufgaben',
    knowledge: 'Wissensdatenbank',
    git: 'Versionsverwaltung'
  }[integrationType] || 'Websuche'
}

function typeIcon(integrationType) {
  return { calendar: 'calendar_month', knowledge: 'menu_book', git: 'account_tree' }[integrationType] || 'travel_explore'
}

function gitExecutionAgentId(integration) {
  return integration.executionAgentId || integration.config?.executionAgentId || null
}

function executionAgentName(integration) {
  return executionAgents.byId(gitExecutionAgentId(integration))?.name
    || integration.executionAgentName
    || integration.config?.executionAgentName
    || 'Nicht verfügbarer Computer'
}

function gitAllowNetwork(integration) {
  return integration.allowNetwork ?? integration.config?.allowNetwork ?? false
}

async function testIntegration(integration) {
  testingId.value = integration.id
  try {
    if (integration.integrationType === 'calendar') await integrations.testCalendar(integration.id)
    else if (integration.integrationType === 'knowledge') await integrations.testKnowledge(integration.id)
    else await integrations.testSearch(integration.id)
    $q.notify({ type: 'positive', message: 'Verbindung erfolgreich' })
  } catch {
    // The central API interceptor displays the error as a toast.
  } finally {
    testingId.value = null
  }
}

async function removeIntegration(integration) {
  const confirmed = await confirm(
    `Integration „${integration.name}“ vollständig löschen?`,
    'Integration löschen',
    { okLabel: 'Löschen' }
  )
  if (!confirmed) return
  if (integration.integrationType === 'calendar') await integrations.removeCalendar(integration.id)
  else if (integration.integrationType === 'knowledge') await integrations.removeKnowledge(integration.id)
  else if (integration.integrationType === 'git') await integrations.removeGit(integration.id)
  else await integrations.removeSearch(integration.id)
  $q.notify({ type: 'positive', message: 'Integration gelöscht' })
}

async function reload() {
  try {
    await Promise.all([
      integrations.fetchSearch(),
      integrations.fetchCalendar(),
      integrations.fetchKnowledge(),
      integrations.fetchGit(),
      executionAgents.fetchAll()
    ])
  } catch {
    // The central API interceptor displays the error as a toast.
  }
}

onMounted(reload)
</script>
