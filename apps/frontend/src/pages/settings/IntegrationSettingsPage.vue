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
      <div class="row items-center q-gutter-sm">
        <SettingsScopeToggle
          :model-value="integrations.scope"
          :is-admin="auth.isAdmin"
          :disable="integrations.loading || gateLoading || testingId !== null || removingId !== null"
          @update:model-value="changeScope"
        />
        <q-btn
          color="primary"
          unelevated
          no-caps
          icon="add"
          label="Integration hinzufügen"
          :disable="personalBlocked || integrations.loading || !canCreateIntegration"
          @click="openCreateDialog"
        />
      </div>
    </header>

    <q-card v-if="auth.isAdmin && integrations.scope === 'instance'" flat class="app-surface-tonal q-mb-md">
      <q-card-section>
        <q-toggle
          :model-value="integrations.personalIntegrationsEnabled"
          color="primary"
          label="Persönliche Integrationen erlauben"
          :disable="gateLoading"
          @update:model-value="togglePersonalIntegrations"
        />
        <div class="text-caption app-muted q-mt-sm q-mb-xs">Erlaubte persönliche Adapter</div>
        <div class="row q-gutter-sm">
          <q-toggle
            v-for="adapter in adapterOptions"
            :key="adapter.value"
            :model-value="integrations.allowedPersonalAdapterTypes.includes(adapter.value)"
            :label="adapter.label"
            color="primary"
            dense
            :disable="gateLoading"
            @update:model-value="toggleAllowedAdapter(adapter.value, $event)"
          />
        </div>
      </q-card-section>
    </q-card>

    <q-banner v-if="integrations.scope === 'personal'" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="person" color="primary" /></template>
      {{ personalBlocked
        ? 'Persönliche Integrationen wurden für diese Instanz deaktiviert. Vorhandene Einstellungen bleiben gespeichert, können aber nicht bearbeitet werden.'
        : 'Hier verwaltest du deine persönlichen Integrationen. Die Instanz legt fest, welche Adapter zulässig sind.' }}
    </q-banner>

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
            <q-item-label caption>Füge einen externen Dienst wie SearXNG, CalDAV, Mealie oder BookStack hinzu.</q-item-label>
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
            <q-item-label v-if="['git', 'openscad'].includes(integration.integrationType)" caption lines="1">
              Computer: {{ executionAgentName(integration) }} ·
              <template v-if="integration.integrationType === 'git'">
                Netzwerkzugriff {{ gitAllowNetwork(integration) ? 'erlaubt' : 'gesperrt' }}
              </template>
              <template v-else>{{ integration.config.binaryPath }}</template>
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
                :disable="!canEditIntegration(integration)"
                :to="{ name: 'settings-integration-access', params: { integrationId: integration.id }, query: { scope: integrations.scope } }"
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
                :disable="!canEditIntegration(integration)"
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
                :disable="!canEditIntegration(integration)"
                @click="openEditDialog(integration)"
              />
              <q-btn
                flat
                round
                dense
                color="negative"
                icon="delete"
                :aria-label="`${integration.name} löschen`"
                :disable="!canEditIntegration(integration)"
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
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'IntegrationSettingsPage' })

const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const auth = useAuthStore()
const executionAgents = useExecutionAgentStore()
const dialogOpen = ref(false)
const selectedIntegration = ref(null)
const testingId = ref(null)
const removingId = ref(null)
const gateLoading = ref(false)
const showAccessPage = computed(() => route.name === 'settings-integration-access')
const searchProviders = computed(() => integrations.search.providers)
const calendarAccounts = computed(() => integrations.calendar.accounts)
const knowledgeBases = computed(() => integrations.knowledge.bases)
const recipeAccounts = computed(() => integrations.recipes.accounts)
const gitInstances = computed(() => integrations.git.instances)
const openScadInstances = computed(() => integrations.openscad.instances)
const configuredIntegrations = computed(
  () => [
    ...searchProviders.value,
    ...calendarAccounts.value,
    ...knowledgeBases.value,
    ...recipeAccounts.value,
    ...gitInstances.value,
    ...openScadInstances.value
  ]
)
const personalBlocked = computed(() => (
  integrations.scope === 'personal' && !integrations.personalIntegrationsEnabled
))
const canCreateIntegration = computed(() => (
  integrations.scope === 'instance'
  || (
    integrations.personalPolicyLoaded
    && integrations.allowedPersonalAdapterTypes.length > 0
  )
))
const adapterOptions = [
  { label: 'SearXNG', value: 'searxng' },
  { label: 'CalDAV', value: 'caldav' },
  { label: 'BookStack', value: 'bookstack' },
  { label: 'Mealie', value: 'mealie' },
  { label: 'Git', value: 'git' },
  { label: 'OpenSCAD', value: 'openscad' }
]

function canEditIntegration(integration) {
  return !personalBlocked.value && integrations.adapterAllowed(integration.providerType)
}

async function changeScope(scope) {
  dialogOpen.value = false
  integrations.selectScope(scope)
  await reload()
}

async function saveIntegrationGate(enabled, allowedAdapterTypes) {
  gateLoading.value = true
  try {
    await integrations.savePersonalSettings(enabled, allowedAdapterTypes)
  } finally {
    gateLoading.value = false
  }
}

function togglePersonalIntegrations(enabled) {
  return saveIntegrationGate(enabled, integrations.allowedPersonalAdapterTypes)
}

function toggleAllowedAdapter(adapterType, enabled) {
  const allowed = new Set(integrations.allowedPersonalAdapterTypes)
  if (enabled) allowed.add(adapterType)
  else allowed.delete(adapterType)
  return saveIntegrationGate(integrations.personalIntegrationsEnabled, [...allowed])
}

function openCreateDialog() {
  selectedIntegration.value = null
  dialogOpen.value = true
}

function openEditDialog(integration) {
  selectedIntegration.value = integration
  dialogOpen.value = true
}

function providerLabel(providerType) {
  return {
    searxng: 'SearXNG', caldav: 'CalDAV', bookstack: 'BookStack', mealie: 'Mealie', git: 'Git', openscad: 'OpenSCAD'
  }[providerType] || providerType
}

function typeLabel(integrationType) {
  return {
    calendar: 'Kalender & Aufgaben',
    knowledge: 'Wissensdatenbank',
    recipes: 'Rezepte & Essensplanung',
    git: 'Versionsverwaltung',
    openscad: '3D-Modellierung'
  }[integrationType] || 'Websuche'
}

function typeIcon(integrationType) {
  return {
    calendar: 'calendar_month', knowledge: 'menu_book', recipes: 'restaurant_menu', git: 'account_tree', openscad: 'view_in_ar'
  }[integrationType] || 'travel_explore'
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
    let result
    if (integration.integrationType === 'calendar') result = await integrations.testCalendar(integration.id)
    else if (integration.integrationType === 'knowledge') result = await integrations.testKnowledge(integration.id)
    else if (integration.integrationType === 'recipes') result = await integrations.testRecipes(integration.id)
    else if (integration.integrationType === 'openscad') result = await integrations.testOpenScad(integration.id)
    else result = await integrations.testSearch(integration.id)
    if (integration.integrationType === 'openscad' && result?.headlessPreview === false) {
      $q.notify({
        type: 'warning',
        message: 'OpenSCAD ist erreichbar, unterstützt aber keine Headless-Vorschau.'
      })
      return
    }
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
  removingId.value = integration.id
  try {
    if (integration.integrationType === 'calendar') await integrations.removeCalendar(integration.id)
    else if (integration.integrationType === 'knowledge') await integrations.removeKnowledge(integration.id)
    else if (integration.integrationType === 'recipes') await integrations.removeRecipes(integration.id)
    else if (integration.integrationType === 'git') await integrations.removeGit(integration.id)
    else if (integration.integrationType === 'openscad') await integrations.removeOpenScad(integration.id)
    else await integrations.removeSearch(integration.id)
    $q.notify({ type: 'positive', message: 'Integration gelöscht' })
  } finally {
    removingId.value = null
  }
}

async function reload() {
  try {
    await Promise.all([integrations.fetchAll(), executionAgents.fetchAll()])
  } catch {
    // The central API interceptor displays the error as a toast.
  }
}

onMounted(() => {
  const scope = route.query.scope === 'personal'
    ? 'personal'
    : (auth.isAdmin ? 'instance' : 'personal')
  integrations.selectScope(scope)
  if (!showAccessPage.value) void reload()
})
</script>
