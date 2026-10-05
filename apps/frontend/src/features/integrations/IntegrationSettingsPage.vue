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
      <div v-if="showInstances" class="settings-scope-actions row items-center col-auto">
        <q-btn
          class="col-auto"
          color="primary"
          unelevated
          no-caps
          no-wrap
          icon="add"
          label="Integration hinzufügen"
          :disable="integrations.loading || integrations.catalogLoading || !canCreateIntegration"
          @click="openCreateDialog"
        />
      </div>
    </header>

    <q-tabs
      v-model="section"
      dense
      no-caps
      align="left"
      active-color="primary"
      indicator-color="primary"
      class="q-mb-lg"
    >
      <q-tab name="general" icon="settings" label="Einstellungen" />
      <q-tab name="management" icon="extension" label="Verwaltung" />
    </q-tabs>

    <q-banner v-if="route.query.profileId && !agents.loading && (!profileAssignmentsAllowed || !profileOptions.some((profile) => profile.value === route.query.profileId))" rounded class="app-surface-tonal q-mb-md">Die Profilzuordnungen für diesen Agenten sind hier nicht verfügbar.</q-banner>

    <template v-if="admin && section === 'general'">
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

    <q-banner v-if="showInstances && !personalCreatable" rounded class="app-surface-tonal q-mb-md">
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

    <q-card v-if="section === 'general'" flat class="app-surface q-mb-md">
      <q-card-section>
        <div class="text-subtitle1 text-weight-bold">{{ admin ? 'Globale Suchprovider' : 'Persönliche Suchprovider' }}</div>
        <div class="text-body2 app-muted q-mt-xs">
          Primären Dienst und optionalen Fallback für die Websuche auswählen.
        </div>
      </q-card-section>
      <q-separator />
      <q-card-section class="row q-col-gutter-md">
        <div class="col-12 col-md-6">
          <q-select dense
            :model-value="searchSelection.primaryProvider"
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
          <q-select dense
            :model-value="searchSelection.fallbackProvider"
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

    <q-card v-if="section === 'general' && profileOptions.length && profileAssignmentsAllowed" flat class="app-surface q-mb-md">
      <q-card-section class="q-gutter-md">
        <div class="text-subtitle1 text-weight-bold">Profilzuordnungen</div>
        <div class="text-body2 app-muted">Legt fest, welche Integrationen ein Profil verwenden darf. Abweichungen an einer persönlichen Verbindung haben Vorrang.</div>
        <q-select dense v-model="assignmentProfileId" outlined emit-value map-options label="Agentenprofil" :options="profileOptions" :disable="assignmentsLoading" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" />
        <q-banner v-if="assignmentsFailed" rounded class="app-surface-tonal">
          Die Profilzuordnungen konnten nicht geladen werden.
          <template #action><q-btn flat no-caps color="primary" label="Erneut laden" @click="loadAssignments" /></template>
        </q-banner>
        <template v-if="assignments">
          <q-select dense :model-value="assignments.integrationsEnabledByDefault" outlined emit-value map-options label="Integrationen standardmäßig" :options="availabilityChoices(assignments.inheritedIntegrationsEnabledByDefault)" :disable="assignmentsLoading" @update:model-value="saveAssignments({ integrationsEnabledByDefault: $event })" />
          <q-select dense v-for="integration in agents.integrations" :key="integration.id" :model-value="assignments.integrationAvailability[integration.id] ?? null" outlined emit-value map-options :label="integration.name" :options="availabilityChoices(assignments.inheritedIntegrationAvailability[integration.id] ?? assignments.integrationsEnabledByDefault ?? assignments.inheritedIntegrationsEnabledByDefault)" :disable="assignmentsLoading" @update:model-value="setIntegrationAvailability(integration.id, $event)" />
        </template>
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
              <q-badge outline color="grey-7" :label="integration.scope === 'instance' ? 'Global' : 'Persönlich'" />
              <q-chip dense :color="integrationAvailable(integration) && integration.enabled ? 'positive' : 'grey-7'" text-color="white">
                {{ integrationAvailable(integration) ? (integration.enabled ? 'Aktiv' : 'Deaktiviert') : 'Nicht verfügbar' }}
              </q-chip>
            </div>
            <q-item-label caption>
              {{ typeLabel(integration.integrationType) }} ·
              {{ providerLabel(integration.providerType) }}
            </q-item-label>
            <q-item-label v-if="integration.integrationType === 'openscad'" caption lines="1">
              Computer: {{ executionAgentName(integration) }} ·
              {{ integration.config.binaryPath }}
            </q-item-label>
            <q-item-label v-else-if="integration.integrationType === 'mail' && integration.config?.imapHost" caption lines="1">
              IMAP: {{ integration.config.imapHost }}:{{ integration.config.imapPort }} · SMTP: {{ integration.config.smtpHost }}:{{ integration.config.smtpPort }}
            </q-item-label>
            <template v-else-if="integration.integrationType === 'browser'">
              <q-item-label caption>Computer: {{ executionAgentName(integration) }}</q-item-label>
              <q-item-label caption role="status">{{ browserStatusLabel(integrations.browserStatuses[integration.id]) }}</q-item-label>
              <div class="row q-gutter-xs q-mt-xs">
                <q-btn flat dense no-caps icon="terminal" label="Einrichtung / Status" :disable="!canEditIntegration(integration)" @click="setupId = integration.id" />
                <q-btn flat dense no-caps color="negative" icon="restart_alt" label="Profil zurücksetzen" :disable="!canEditIntegration(integration) || resettingId !== null" @click="resetBrowser(integration)" />
              </div>
            </template>
            <q-item-label v-else caption lines="1">
              {{ integration.config?.baseUrl || (integration.scope === 'instance' ? 'Globale Verbindung' : '') }}{{ integration.config?.username ? ` · ${integration.config.username}` : '' }}
            </q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row items-center no-wrap q-gutter-xs">
              <q-toggle
                dense
                :model-value="integration.enabled"
                :aria-label="`${integration.name} ${integration.enabled ? 'deaktivieren' : 'aktivieren'}`"
                :disable="!canEditIntegration(integration) || togglingId !== null"
                @update:model-value="toggleIntegration(integration, $event)"
              />
              <q-btn
                flat
                dense
                no-caps
                :round="$q.screen.lt.md"
                icon="folder_open"
                :label="$q.screen.gt.sm ? 'Berechtigungen' : undefined"
                aria-label="Integrationsrechte verwalten"
                :disable="!canEditIntegration(integration)"
                :to="{ name: admin ? 'admin-integration-resources' : 'settings-integration-resources', params: { integrationId: integration.id } }"
              />
              <q-btn
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
                :disable="removingId !== null || (integration.scope === 'instance' && !auth.isAdmin)"
                @click="removeIntegration(integration)"
              />
            </div>
          </q-item-section>
        </q-item>
      </q-list>
      <q-inner-loading :showing="integrations.loading || (integrations.catalogLoading && !integrations.catalogLoaded)" />
    </q-card>

    <IntegrationConfigDialog v-if="showInstances" v-model="dialogOpen" :integration="selectedIntegration" @update:model-value="onDialogChange" />
    <BrowserSetupDialog v-if="setupId" :model-value="Boolean(setupId)" :integration-id="setupId" @update:model-value="setupId = null" />
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import { api } from 'src/boot/axios'
import IntegrationConfigDialog from 'src/features/integrations/IntegrationConfigDialog.vue'
import BrowserSetupDialog from 'src/features/browser/BrowserSetupDialog.vue'
import { browserStatusLabel } from 'src/utils/browser'
import { useConfirm } from 'src/composables/confirm'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'
import { useAuthStore } from 'stores/auth'
import { useAgentProfileStore } from 'stores/agent-profiles'

defineOptions({ name: 'IntegrationSettingsPage' })
const { admin } = defineProps({ admin: { type: Boolean, default: false } })

const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const auth = useAuthStore()
const agents = useAgentProfileStore()
const assignmentProfileId = ref(null)
const assignments = ref(null)
const assignmentsLoading = ref(false)
const assignmentsFailed = ref(false)
let assignmentsRevision = 0
const profileAssignmentsAllowed = computed(() => admin || auth.isAdmin || agents.personalAgentsEnabled && agents.personalToolPermissionsEnabled)
const profileOptions = computed(() => agents.manageableAgents.filter((agent) => admin ? agent.scope === 'instance' && agent.protected : agent.scope === 'personal').map((agent) => ({ label: agent.name, value: agent.id })))
const availabilityChoices = (inherited) => [{ label: `Erben: ${inherited ? 'Verfügbar' : 'Nicht verfügbar'}`, value: null }, { label: 'Verfügbar', value: true }, { label: 'Nicht verfügbar', value: false }]
const executionAgents = useExecutionAgentStore()
const dialogOpen = ref(false)
const selectedIntegration = ref(null)
const setupId = ref(null)
const resettingId = ref(null)
const testingId = ref(null)
const removingId = ref(null)
const togglingId = ref(null)
const searchSelectionSaving = ref(false)
const section = ref('general')
const instanceIntegrations = computed(() => [
  ...integrations.search.providers, ...integrations.calendar.accounts,
  ...integrations.mail.accounts, ...integrations.knowledge.bases,
  ...integrations.recipes.accounts, ...integrations.arcane.instances, ...integrations.music.instances
])
const storedPersonalIntegrations = ref([])
const searchSelection = ref({ primaryProvider: 'no_key', fallbackProvider: null, providerOptions: [{ name: 'Websuche ohne API-Key', value: 'no_key' }] })
const showAccessPage = computed(() => ['settings-integration-resources', 'admin-integration-resources'].includes(route.name))
const showInstances = computed(() => section.value === 'management')
const searchProviderOptions = computed(() => searchSelection.value.providerOptions.map((provider) => ({
  label: provider.integrationId ? `${provider.name} · SearXNG` : provider.name,
  value: provider.value
})))
const fallbackSearchProviderOptions = computed(() => searchProviderOptions.value.filter(
  (provider) => provider.value !== searchSelection.value.primaryProvider
))
const personalIntegrations = computed(
  () => [
    ...instanceIntegrations.value,
    ...integrations.tbsDesk.accounts,
    ...integrations.openscad.instances,
    ...integrations.coding.instances,
    ...integrations.browser.instances.map((item) => ({ ...item, integrationType: 'browser', providerType: 'browser' }))
  ]
)
const configuredIntegrations = computed(() => (admin ? instanceIntegrations.value.map((item) => ({ ...item, scope: 'instance' })) : [
  ...personalIntegrations.value.map((item) => ({ ...item, scope: 'personal' })),
  ...storedPersonalIntegrations.value.filter((item) => !personalIntegrations.value.some((own) => own.id === item.id)),
]))
const backendAvailabilityOptions = [
  { label: 'Global und persönlich', value: 'full' },
  { label: 'Nur global', value: 'instance_only' },
  { label: 'Nicht verfügbar', value: 'disabled' }
]
const agentAvailabilityOptions = [
  { label: 'Persönlich verfügbar', value: 'full' },
  { label: 'Nicht verfügbar', value: 'disabled' }
]
const personalCreatable = computed(() => integrations.catalog.some((adapter) => integrations.adapterAvailable(adapter.adapterType, 'personal')))
const canCreateIntegration = computed(() => (
  integrations.catalogLoaded
   && integrations.catalog.some((adapter) => integrations.adapterAvailable(adapter.adapterType, admin ? 'instance' : 'personal'))
))

function availabilityOptions(adapter) {
  return adapter.executionLocation === 'agent' || adapter.personalOnly
    ? agentAvailabilityOptions
    : backendAvailabilityOptions
}

function canEditIntegration(integration) {
  return (integration.scope === 'personal' || auth.isAdmin) && integrationAvailable(integration)
}

function integrationAvailable(integration) {
  return integrations.adapterAvailable(integration.providerType, integration.scope)
}

async function toggleIntegration(integration, enabled) {
  togglingId.value = integration.id
  try {
    await integrations.setIntegrationEnabled(integration.id, enabled, integration.scope)
    await reload()
    $q.notify({ type: 'positive', message: enabled ? 'Integration aktiviert' : 'Integration deaktiviert' })
  } catch {
    // The central API interceptor displays the error.
  } finally {
    togglingId.value = null
  }
}

async function saveSearchSelection(primaryProvider, fallbackProvider) {
  searchSelectionSaving.value = true
  try {
    const { data } = await api.put('/integrations/search', { primaryProvider, fallbackProvider }, { params: { scope: admin ? 'instance' : 'personal' } })
    searchSelection.value = data
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
    searchSelection.value.fallbackProvider === primaryProvider
      ? null
      : searchSelection.value.fallbackProvider
  )
}

async function setFallbackSearchProvider(fallbackProvider) {
  await saveSearchSelection(searchSelection.value.primaryProvider, fallbackProvider)
}

function openCreateDialog() {
  integrations.selectScope(admin ? 'instance' : 'personal')
  selectedIntegration.value = null
  dialogOpen.value = true
}

function openEditDialog(integration) {
  integrations.selectScope(integration.scope)
  selectedIntegration.value = integration
  dialogOpen.value = true
}

function onDialogChange(open) {
  if (!open) void reload()
}

function providerLabel(providerType) {
  return {
    searxng: 'SearXNG', caldav: 'CalDAV', imap_smtp: 'IMAP/SMTP', 'tbs-desk': 'tbs.Desk', bookstack: 'BookStack', mealie: 'Mealie', arcane: 'Arcane', sonobin: 'SonoBin', openscad: 'OpenSCAD', opencode: 'OpenCode', browser: 'Chromium'
  }[providerType] || providerType
}

function typeLabel(integrationType) {
  return {
    search: 'Websuche',
    calendar: 'Kalender & Aufgaben',
    mail: 'E-Mail',
    support: 'Support',
    knowledge: 'Wissensdatenbank',
    recipes: 'Rezepte & Essensplanung',
    infrastructure: 'Docker-Verwaltung',
    music: 'Musikbibliothek',
    openscad: '3D-Modellierung',
    coding: 'Programmierung',
    browser: 'Browser'
  }[integrationType] || integrationType || 'Unbekannter Integrationstyp'
}

function typeIcon(integrationType) {
  return {
    search: 'travel_explore', calendar: 'calendar_month', mail: 'mail', support: 'confirmation_number', knowledge: 'menu_book', recipes: 'restaurant_menu', infrastructure: 'dns', music: 'library_music', openscad: 'view_in_ar', coding: 'code', browser: 'web'
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

function executionAgentName(integration) {
  const agentId = integration.executionAgentId || integration.config?.executionAgentId || null
  return executionAgents.byId(agentId)?.alias
    || integration.executionAgentName
    || integration.config?.executionAgentName
    || 'Nicht verfügbarer Computer'
}

async function testIntegration(integration) {
  integrations.selectScope(integration.scope)
  testingId.value = integration.id
  try {
    let result
    if (integration.integrationType === 'calendar') result = await integrations.testCalendar(integration.id)
    else if (integration.integrationType === 'mail') result = await integrations.testMail(integration.id)
    else if (integration.integrationType === 'support') result = await integrations.testTbsDesk(integration.id)
    else if (integration.integrationType === 'knowledge') result = await integrations.testKnowledge(integration.id)
    else if (integration.integrationType === 'recipes') result = await integrations.testRecipes(integration.id)
    else if (integration.integrationType === 'infrastructure') result = await integrations.testArcane(integration.id)
    else if (integration.integrationType === 'music') result = await integrations.testSonoBin(integration.id)
    else if (integration.integrationType === 'openscad') result = await integrations.testOpenScad(integration.id)
    else if (integration.integrationType === 'coding') result = await integrations.testOpenCode(integration.id)
    else if (integration.integrationType === 'browser') {
      result = await integrations.browserStatus(integration.id, true)
      $q.notify({ type: result.state === 'ready' ? 'positive' : 'warning', message: browserStatusLabel(result) })
      return
    }
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
    if (integration.scope === 'instance') await reload()
  }
}

async function removeIntegration(integration) {
  const confirmed = await confirm(
    `Integration „${integration.name}“ vollständig löschen?`,
    'Integration löschen',
    { okLabel: 'Löschen' }
  )
  if (!confirmed) return
  integrations.selectScope(integration.scope)
  removingId.value = integration.id
  try {
    if (integration.integrationType === 'calendar') await integrations.removeCalendar(integration.id)
    else if (integration.integrationType === 'mail') await integrations.removeMail(integration.id)
    else if (integration.integrationType === 'support') await integrations.removeTbsDesk(integration.id)
    else if (integration.integrationType === 'knowledge') await integrations.removeKnowledge(integration.id)
    else if (integration.integrationType === 'recipes') await integrations.removeRecipes(integration.id)
    else if (integration.integrationType === 'infrastructure') await integrations.removeArcane(integration.id)
    else if (integration.integrationType === 'music') await integrations.removeSonoBin(integration.id)
    else if (integration.integrationType === 'openscad') await integrations.removeOpenScad(integration.id)
    else if (integration.integrationType === 'coding') await integrations.removeOpenCode(integration.id)
    else if (integration.integrationType === 'browser') await integrations.removeBrowser(integration.id)
    else await integrations.removeSearch(integration.id)
    $q.notify({ type: 'positive', message: 'Integration gelöscht' })
  } finally {
    removingId.value = null
    await reload()
  }
}

async function reload() {
  try {
    integrations.selectScope(admin ? 'instance' : 'personal')
    await Promise.all([
      integrations.fetchCatalog(),
      integrations.fetchAll(),
      agents.fetchAgents(admin ? 'instance' : 'personal'),
      executionAgents.fetchAll()
    ])
    searchSelection.value = { ...integrations.search }
    const visible = (await api.get('/integrations/visible')).data
    storedPersonalIntegrations.value = visible.filter((item) => item.scope === 'personal')
    selectRequestedProfile()
  } catch {
    // The central API interceptor displays the error as a toast.
  }
}

async function resetBrowser(integration) {
  if (!await confirm('Alle Browser-Anmeldungen, Cookies und Profildaten auf diesem Computer löschen? Die Installation bleibt erhalten. Schließe vorher alle Browser-Verbindungen.', 'Browserprofil zurücksetzen?', { okLabel: 'Zurücksetzen', okColor: 'negative' })) return
  resettingId.value = integration.id
  try {
    await integrations.resetBrowser(integration.id)
    $q.notify({ type: 'positive', message: 'Browserprofil zurückgesetzt' })
  } catch { /* Central API toast. */ }
  finally { resettingId.value = null }
}

async function loadAssignments() {
  const revision = ++assignmentsRevision
  assignments.value = null
  assignmentsFailed.value = false
  assignmentsLoading.value = Boolean(assignmentProfileId.value)
  if (!assignmentProfileId.value) return
  try {
    const data = await integrations.fetchProfileAvailability(assignmentProfileId.value, admin ? 'instance' : 'personal')
    if (revision === assignmentsRevision) assignments.value = data
  } catch { if (revision === assignmentsRevision) assignmentsFailed.value = true }
  finally { if (revision === assignmentsRevision) assignmentsLoading.value = false }
}

function selectRequestedProfile() {
  const id = route.query.profileId
  if (!id) return
  section.value = 'general'
  assignmentProfileId.value = profileAssignmentsAllowed.value && profileOptions.value.some((profile) => profile.value === id) ? id : null
}

async function saveAssignments(changes) {
  const revision = ++assignmentsRevision
  assignmentsLoading.value = true
  try {
    const data = await integrations.saveProfileAvailability(assignmentProfileId.value, admin ? 'instance' : 'personal', {
      integrationsEnabledByDefault: assignments.value.integrationsEnabledByDefault,
      integrationAvailability: assignments.value.integrationAvailability,
      ...changes
    })
    if (revision !== assignmentsRevision) return
    assignments.value = data
    $q.notify({ type: 'positive', message: 'Profilzuordnungen gespeichert' })
  } catch { /* Central API toast. */ }
  finally { if (revision === assignmentsRevision) assignmentsLoading.value = false }
}

function setIntegrationAvailability(id, value) {
  const integrationAvailability = { ...assignments.value.integrationAvailability }
  if (value === null) delete integrationAvailability[id]
  else integrationAvailability[id] = value
  return saveAssignments({ integrationAvailability })
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
  if (!showAccessPage.value) void reload()
})
watch(() => admin, () => { assignmentProfileId.value = null; assignments.value = null; void reload() })
watch(assignmentProfileId, loadAssignments)
watch(() => route.query.profileId, selectRequestedProfile)
</script>
