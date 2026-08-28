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
      <div v-if="showInstances" class="row items-center q-gutter-sm">
        <SettingsScopeToggle
          :model-value="integrations.scope"
          :is-admin="auth.isAdmin"
          :disable="integrations.loading || integrations.catalogLoading || testingId !== null || removingId !== null"
          @update:model-value="changeScope"
        />
        <q-btn
          color="primary"
          unelevated
          no-caps
          icon="add"
          label="Integration hinzufügen"
          :disable="integrations.loading || integrations.catalogLoading || !canCreateIntegration"
          @click="openCreateDialog"
        />
      </div>
    </header>

    <q-tabs
      v-if="auth.isAdmin"
      v-model="section"
      dense
      no-caps
      align="left"
      active-color="primary"
      indicator-color="primary"
      class="q-mb-lg"
    >
      <q-tab name="catalog" icon="extension" label="Verfügbare Integrationen" />
      <q-tab name="instances" icon="settings" label="Integrationsinstanzen" />
    </q-tabs>

    <template v-if="auth.isAdmin && section === 'catalog'">
      <q-banner v-if="integrations.catalogLoadFailed" rounded class="app-surface-tonal q-mb-md">
        Die verfügbaren Integrationen konnten nicht geladen werden.
        <template #action>
          <q-btn flat no-caps color="primary" label="Erneut laden" @click="loadCatalog" />
        </template>
      </q-banner>

      <q-card flat class="app-surface">
        <q-list separator>
          <q-item
            v-if="integrations.catalogLoaded && !integrations.catalog.length"
            class="q-py-lg"
          >
            <q-item-section avatar>
              <q-avatar color="grey-7" text-color="white" icon="extension" />
            </q-item-section>
            <q-item-section>
              <q-item-label>Keine Integrationsadapter registriert</q-item-label>
            </q-item-section>
          </q-item>

          <q-item v-for="adapter in integrations.catalog" :key="adapter.adapterType" class="q-py-md">
            <q-item-section>
              <div class="row items-center q-col-gutter-md">
                <div class="col-12 col-sm">
                  <div class="row items-center no-wrap q-gutter-sm">
                    <q-avatar color="primary" text-color="white" :icon="typeIcon(adapter.integrationType)" />
                    <div>
                      <div class="row items-center q-gutter-sm">
                        <q-item-label class="text-weight-medium">{{ providerLabel(adapter.adapterType) }}</q-item-label>
                        <q-chip
                          dense
                          outline
                          color="grey-6"
                          :icon="executionLocationIcon(adapter.executionLocation)"
                        >
                          {{ executionLocationLabel(adapter.executionLocation) }}
                        </q-chip>
                      </div>
                      <q-item-label caption>{{ typeLabel(adapter.integrationType) }}</q-item-label>
                    </div>
                  </div>
                </div>
                <div class="col-12 col-sm-5">
                  <q-select
                    :model-value="adapter.availability"
                    outlined
                    dense
                    emit-value
                    map-options
                    label="Verfügbarkeit"
                    :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                    :options="availabilityOptions(adapter)"
                    :disable="integrations.catalogLoading"
                    :loading="integrations.catalogLoading"
                    @update:model-value="setAdapterAvailability(adapter.adapterType, $event)"
                  />
                </div>
              </div>
            </q-item-section>
          </q-item>
        </q-list>
        <q-inner-loading :showing="integrations.catalogLoading" />
      </q-card>
    </template>

    <q-banner v-if="showInstances && integrations.scope === 'personal'" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="person" color="primary" /></template>
      Die Verfügbarkeit wird je Integrationstyp festgelegt. Nur persönlich verfügbare Typen können hinzugefügt oder bearbeitet werden. Vorhandene Integrationen anderer Typen bleiben sichtbar und können gelöscht werden.
    </q-banner>

    <q-banner v-if="showInstances && integrations.loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Integrationen konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="primary" label="Erneut laden" @click="reload" />
      </template>
    </q-banner>

    <q-banner v-if="showInstances && integrations.catalogLoadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Verfügbarkeit der Integrationen konnte nicht geladen werden. Es können derzeit keine Integrationen bearbeitet oder hinzugefügt werden.
      <template #action>
        <q-btn flat no-caps color="primary" label="Erneut laden" @click="loadCatalog" />
      </template>
    </q-banner>

    <q-card v-if="showInstances" flat class="app-surface q-mb-md">
      <q-card-section>
        <div class="text-subtitle1 text-weight-bold">Suchprovider</div>
        <div class="text-body2 app-muted q-mt-xs">
          Primären Dienst und optionalen Fallback für die Websuche auswählen.
        </div>
      </q-card-section>
      <q-separator />
      <q-card-section class="row q-col-gutter-md">
        <div class="col-12 col-md-6">
          <q-select
            :model-value="integrations.search.primaryProvider"
            outlined
            emit-value
            map-options
            label="Primärprovider"
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
            :options="searchProviderOptions"
            :loading="searchSelectionSaving"
            :disable="integrations.loading || searchSelectionSaving"
            @update:model-value="setPrimarySearchProvider"
          />
        </div>
        <div class="col-12 col-md-6">
          <q-select
            :model-value="integrations.search.fallbackProvider"
            outlined
            emit-value
            map-options
            clearable
            label="Fallback-Provider"
            hint="Wird nur bei temporären Fehlern des Primärproviders verwendet."
            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
            :options="fallbackSearchProviderOptions"
            :loading="searchSelectionSaving"
            :disable="integrations.loading || searchSelectionSaving"
            @update:model-value="setFallbackSearchProvider"
          />
        </div>
      </q-card-section>
    </q-card>

    <q-card v-show="showInstances" flat class="app-surface integration-list-card">
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
              <q-chip dense :color="integrationAvailable(integration) && integration.enabled ? 'positive' : 'grey-7'" text-color="white">
                {{ integrationAvailable(integration) ? (integration.enabled ? 'Aktiv' : 'Deaktiviert') : 'Nicht verfügbar' }}
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
            <q-item-label v-else-if="integration.integrationType === 'mail'" caption lines="1">
              IMAP: {{ integration.config.imapHost }}:{{ integration.config.imapPort }} · SMTP: {{ integration.config.smtpHost }}:{{ integration.config.smtpPort }}
            </q-item-label>
            <q-item-label v-else caption lines="1">
              {{ integration.config.baseUrl }}{{ integration.config.username ? ` · ${integration.config.username}` : '' }}
            </q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row items-center no-wrap q-gutter-xs">
              <q-btn
                v-if="integration.supportsIntegrationPolicies || integration.supportsResourcePolicies"
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
                :loading="removingId === integration.id"
                :disable="removingId !== null"
                @click="removeIntegration(integration)"
              />
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <q-inner-loading :showing="integrations.loading || (integrations.catalogLoading && !integrations.catalogLoaded)" />
    </q-card>

    <IntegrationConfigDialog v-if="showInstances" v-model="dialogOpen" :integration="selectedIntegration" />
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import IntegrationConfigDialog from 'components/IntegrationConfigDialog.vue'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
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
const searchSelectionSaving = ref(false)
const section = ref('instances')
const showAccessPage = computed(() => route.name === 'settings-integration-access')
const showInstances = computed(() => !auth.isAdmin || section.value === 'instances')
const searchProviders = computed(() => integrations.search.providers)
const calendarAccounts = computed(() => integrations.calendar.accounts)
const mailAccounts = computed(() => integrations.mail.accounts)
const knowledgeBases = computed(() => integrations.knowledge.bases)
const recipeAccounts = computed(() => integrations.recipes.accounts)
const gitInstances = computed(() => integrations.git.instances)
const openScadInstances = computed(() => integrations.openscad.instances)
const codingInstances = computed(() => integrations.coding.instances)
const searchProviderOptions = computed(() => integrations.search.providerOptions.map((provider) => ({
  label: provider.integrationId ? `${provider.name} · SearXNG` : provider.name,
  value: provider.value
})))
const fallbackSearchProviderOptions = computed(() => searchProviderOptions.value.filter(
  (provider) => provider.value !== integrations.search.primaryProvider
))
const configuredIntegrations = computed(
  () => [
    ...searchProviders.value,
    ...calendarAccounts.value,
    ...mailAccounts.value,
    ...knowledgeBases.value,
    ...recipeAccounts.value,
    ...gitInstances.value,
    ...openScadInstances.value,
    ...codingInstances.value
  ]
)
const backendAvailabilityOptions = [
  { label: 'Instanz und persönlich', value: 'full' },
  { label: 'Nur Instanz', value: 'instance_only' },
  { label: 'Nicht verfügbar', value: 'disabled' }
]
const agentAvailabilityOptions = [
  { label: 'Persönlich verfügbar', value: 'full' },
  { label: 'Nicht verfügbar', value: 'disabled' }
]
const canCreateIntegration = computed(() => (
  integrations.catalogLoaded
  && integrations.catalog.some((adapter) => integrations.adapterAvailable(adapter.adapterType))
))

function availabilityOptions(adapter) {
  return adapter.executionLocation === 'agent' || adapter.personalOnly
    ? agentAvailabilityOptions
    : backendAvailabilityOptions
}

function canEditIntegration(integration) {
  return integrationAvailable(integration)
}

function integrationAvailable(integration) {
  return integrations.adapterAvailable(integration.providerType)
}

async function changeScope(scope) {
  dialogOpen.value = false
  integrations.selectScope(scope)
  await reload()
}

async function saveSearchSelection(primaryProvider, fallbackProvider) {
  searchSelectionSaving.value = true
  try {
    await integrations.saveSearchSelection(primaryProvider, fallbackProvider)
    $q.notify({ type: 'positive', message: 'Suchprovider gespeichert' })
  } catch {
    // The central API interceptor displays the error as a toast.
  } finally {
    searchSelectionSaving.value = false
  }
}

async function setPrimarySearchProvider(primaryProvider) {
  await saveSearchSelection(
    primaryProvider,
    integrations.search.fallbackProvider === primaryProvider
      ? null
      : integrations.search.fallbackProvider
  )
}

async function setFallbackSearchProvider(fallbackProvider) {
  await saveSearchSelection(integrations.search.primaryProvider, fallbackProvider)
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
    searxng: 'SearXNG', caldav: 'CalDAV', imap_smtp: 'IMAP/SMTP', bookstack: 'BookStack', mealie: 'Mealie', git: 'Git', openscad: 'OpenSCAD', opencode: 'OpenCode'
  }[providerType] || providerType
}

function typeLabel(integrationType) {
  return {
    search: 'Websuche',
    calendar: 'Kalender & Aufgaben',
    mail: 'E-Mail',
    knowledge: 'Wissensdatenbank',
    recipes: 'Rezepte & Essensplanung',
    git: 'Versionsverwaltung',
    openscad: '3D-Modellierung',
    coding: 'Programmierung'
  }[integrationType] || integrationType || 'Unbekannter Integrationstyp'
}

function typeIcon(integrationType) {
  return {
    search: 'travel_explore', calendar: 'calendar_month', mail: 'mail', knowledge: 'menu_book', recipes: 'restaurant_menu', git: 'account_tree', openscad: 'view_in_ar', coding: 'code'
  }[integrationType] || 'extension'
}

function executionLocationLabel(executionLocation) {
  if (executionLocation === 'agent') return 'Computer'
  if (executionLocation === 'backend') return 'Backend'
  return 'Unbekannt'
}

function executionLocationIcon(executionLocation) {
  if (executionLocation === 'agent') return 'computer'
  if (executionLocation === 'backend') return 'dns'
  return 'help_outline'
}

function gitExecutionAgentId(integration) {
  return integration.executionAgentId || integration.config?.executionAgentId || null
}

function executionAgentName(integration) {
  return executionAgents.byId(gitExecutionAgentId(integration))?.alias
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
    else if (integration.integrationType === 'mail') result = await integrations.testMail(integration.id)
    else if (integration.integrationType === 'knowledge') result = await integrations.testKnowledge(integration.id)
    else if (integration.integrationType === 'recipes') result = await integrations.testRecipes(integration.id)
    else if (integration.integrationType === 'openscad') result = await integrations.testOpenScad(integration.id)
    else if (integration.integrationType === 'coding') result = await integrations.testOpenCode(integration.id)
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
    else if (integration.integrationType === 'mail') await integrations.removeMail(integration.id)
    else if (integration.integrationType === 'knowledge') await integrations.removeKnowledge(integration.id)
    else if (integration.integrationType === 'recipes') await integrations.removeRecipes(integration.id)
    else if (integration.integrationType === 'git') await integrations.removeGit(integration.id)
    else if (integration.integrationType === 'openscad') await integrations.removeOpenScad(integration.id)
    else if (integration.integrationType === 'coding') await integrations.removeOpenCode(integration.id)
    else await integrations.removeSearch(integration.id)
    $q.notify({ type: 'positive', message: 'Integration gelöscht' })
  } finally {
    removingId.value = null
  }
}

async function reload() {
  try {
    await Promise.all([
      integrations.fetchCatalog(),
      integrations.fetchAll(),
      executionAgents.fetchAll()
    ])
  } catch {
    // The central API interceptor displays the error as a toast.
  }
}

async function loadCatalog() {
  try {
    await integrations.fetchCatalog()
  } catch {
    // The central API interceptor displays the error as a toast.
  }
}

async function setAdapterAvailability(adapterType, availability) {
  try {
    await integrations.setAdapterAvailability(adapterType, availability)
  } catch {
    // The central API interceptor displays the error as a toast.
  }
}

onMounted(() => {
  integrations.selectScope(resolveSettingsScope(route.query.scope, auth.isAdmin))
  if (!showAccessPage.value) void reload()
})
</script>
