<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Provider</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          KI-Verbindungen und ihre verfügbaren Chat- und Medienmodelle verwalten.
        </p>
      </div>
      <div class="provider-settings-actions">
        <SettingsScopeToggle
          :model-value="providers.scope"
          :is-admin="auth.isAdmin"
          :disable="scopeBusy"
          @update:model-value="changeScope"
        />
        <q-toggle
          :model-value="providers.enabled"
          color="primary"
          label="LLM-Laufzeit aktiv"
          :disable="runtimeToggleLoading || personalBlocked"
          @update:model-value="toggleRuntime"
        />
        <q-btn
          color="primary"
          unelevated
          no-caps
          icon="add"
          label="Provider hinzufügen"
          :disable="personalBlocked"
          @click="openCreateDialog"
        />
      </div>
    </header>

    <q-card v-if="auth.isAdmin && providers.scope === 'instance'" flat class="app-surface-tonal q-mb-md">
      <q-card-section class="row items-center justify-between q-gutter-md">
        <div>
          <div class="text-subtitle2 text-weight-bold">Persönliche Provider</div>
          <div class="text-caption app-muted">Erlaubt Benutzern eigene Provider und Modelle zu verwalten.</div>
        </div>
        <q-toggle
          :model-value="providers.personalProvidersEnabled"
          color="primary"
          label="Erlaubt"
          :disable="gateLoading"
          @update:model-value="togglePersonalProviders"
        />
      </q-card-section>
    </q-card>

    <q-banner v-if="providers.scope === 'personal'" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="person" color="primary" /></template>
      {{ personalBlocked
        ? 'Persönliche Provider wurden für diese Instanz deaktiviert. Vorhandene Einstellungen bleiben gespeichert, können aber nicht bearbeitet werden.'
        : 'Hier verwaltest du deine persönlichen Provider und Modelle. Instanzmodelle bleiben zusätzlich für Standardmodelle verfügbar.' }}
    </q-banner>

    <q-card v-if="!llmProviders.length && !providers.loading" flat class="app-surface q-pa-lg">
      <div class="row items-center no-wrap q-gutter-md">
        <q-avatar color="grey-7" text-color="white" icon="hub" />
        <div>
          <div class="text-weight-medium">Noch kein Provider konfiguriert</div>
          <div class="text-body2 app-muted">Füge ChatGPT Codex, Lemonade oder einen OpenAI-kompatiblen Endpunkt hinzu.</div>
        </div>
      </div>
    </q-card>

    <div class="provider-card-list">
      <q-card
        v-for="provider in llmProviders"
        :key="provider.id"
        flat
        class="app-surface provider-card"
      >
        <q-expansion-item v-model="expandedProviders[provider.id]" expand-separator>
          <template #header>
            <q-item-section avatar>
              <q-avatar color="primary" text-color="white" :icon="providerIcon(provider)" />
            </q-item-section>
            <q-item-section class="provider-card__identity">
              <div class="row items-center q-gutter-sm">
                <q-item-label class="text-weight-bold">{{ provider.name }}</q-item-label>
                <q-chip dense :color="providerStatus(provider).color" text-color="white">
                  {{ providerStatus(provider).label }}
                </q-chip>
              </div>
              <q-item-label caption class="ellipsis">{{ providerDescription(provider) }}</q-item-label>
              <q-item-label caption>{{ activeModelCount(provider) }} aktiv · {{ providerModels(provider).length }} Modelle</q-item-label>
            </q-item-section>
            <q-item-section side class="provider-card__header-actions">
              <q-toggle
                :model-value="provider.enabled"
                color="primary"
                label="Provider aktiv"
                :aria-label="`${provider.name}: Provider aktiv`"
                :disable="personalBlocked || !providers.enabled || providerToggleLoading[provider.id]"
                @click.stop
                @update:model-value="toggleProvider(provider, $event)"
              />
              <div @click.stop>
                <q-btn flat round dense icon="more_vert" :aria-label="`Aktionen für ${provider.name}`">
                  <q-menu class="app-menu-popup app-action-menu">
                    <q-list dense style="min-width: 190px">
                      <q-item v-close-popup clickable :disable="personalBlocked" @click="openEditDialog(provider)">
                        <q-item-section avatar><q-icon name="edit" /></q-item-section>
                        <q-item-section>Bearbeiten</q-item-section>
                      </q-item>
                      <q-item v-close-popup clickable :disable="personalBlocked || !canTest(provider)" @click="testProvider(provider.id)">
                        <q-item-section avatar><q-icon name="network_check" /></q-item-section>
                        <q-item-section>Verbindung testen</q-item-section>
                        <q-inner-loading :showing="testingProviderId === provider.id" />
                      </q-item>
                      <q-separator />
                      <q-item v-close-popup clickable :disable="personalBlocked" class="text-negative" @click="removeProvider(provider)">
                        <q-item-section avatar><q-icon name="delete" /></q-item-section>
                        <q-item-section>Löschen</q-item-section>
                      </q-item>
                    </q-list>
                  </q-menu>
                </q-btn>
              </div>
            </q-item-section>
          </template>

          <q-linear-progress v-if="syncing[provider.id]" indeterminate color="primary" />
          <q-card-section class="provider-models-section">
            <div class="provider-models-toolbar">
              <div>
                <div class="text-subtitle1 text-weight-bold">Modelle</div>
                <div v-if="provider.modelsLastSyncedAt" class="text-caption app-muted">
                  Zuletzt aktualisiert: {{ formatDate(provider.modelsLastSyncedAt) }}
                </div>
              </div>
              <q-space />
              <q-btn
                flat
                round
                dense
                icon="refresh"
                aria-label="Modelle aktualisieren"
                :loading="syncing[provider.id]"
                :disable="personalBlocked || !supportsDiscovery(provider)"
                @click="syncProviderModels(provider)"
              >
                <q-tooltip>{{ supportsDiscovery(provider) ? 'Modelle vom Provider aktualisieren' : 'Automatische Erkennung wird nicht unterstützt' }}</q-tooltip>
              </q-btn>
              <q-btn
                v-if="showProminentManualAction(provider) && providerModels(provider).length"
                outline
                no-caps
                color="primary"
                icon="add"
                label="Modell manuell hinzufügen"
                :disable="personalBlocked"
                @click="openModelDialog(provider)"
              />
              <q-btn v-else flat round dense icon="more_vert" aria-label="Weitere Modellaktionen">
                <q-menu class="app-menu-popup app-action-menu">
                  <q-list dense style="min-width: 230px">
                    <q-item v-close-popup clickable :disable="personalBlocked" @click="openModelDialog(provider)">
                      <q-item-section avatar><q-icon name="add" /></q-item-section>
                      <q-item-section>Modell manuell hinzufügen</q-item-section>
                    </q-item>
                  </q-list>
                </q-menu>
              </q-btn>
            </div>

            <q-banner v-if="syncFailed[provider.id]" rounded class="app-surface-tonal q-mt-md">
              <template #avatar><q-icon name="sync_problem" color="warning" /></template>
              Die Modelle konnten nicht automatisch ermittelt werden. Bestehende Einträge bleiben erhalten.
            </q-banner>

            <q-input
              v-if="providerModels(provider).length > 6"
              v-model="modelSearch[provider.id]"
              outlined
              dense
              clearable
              class="q-mt-md"
              label="Modelle durchsuchen"
            >
              <template #prepend><q-icon name="search" /></template>
            </q-input>

            <div v-if="!providerModels(provider).length" class="provider-models-empty app-surface-tonal q-mt-md">
              <q-icon name="view_in_ar" size="32px" color="grey-7" />
              <div class="text-weight-medium q-mt-sm">Keine Modelle verfügbar</div>
              <div class="text-body2 app-muted q-mb-md">
                Starte die Erkennung erneut oder hinterlege ein Modell manuell.
              </div>
              <q-btn
                unelevated
                no-caps
                color="primary"
                icon="add"
                label="Modell manuell hinzufügen"
                :disable="personalBlocked"
                @click="openModelDialog(provider)"
              />
            </div>

            <q-list v-else separator class="provider-model-list q-mt-md">
              <q-item v-for="model in filteredModels(provider)" :key="model.id" class="provider-model-item">
                <q-item-section avatar>
                  <q-avatar rounded color="primary" text-color="white" :icon="modelIcon(model)" size="38px" />
                </q-item-section>
                <q-item-section>
                  <q-item-label class="text-weight-medium">{{ model.name || model.modelId }}</q-item-label>
                  <q-item-label caption class="model-technical-id">{{ model.modelId }}</q-item-label>
                  <div class="row items-center q-gutter-xs q-mt-xs">
                    <q-chip dense outline>{{ model.origin === 'manual' ? 'Manuell' : 'Entdeckt' }}</q-chip>
                    <q-chip
                      v-for="capability in normalizeModelCapabilities(model.capabilities)"
                      :key="capability"
                      dense
                      outline
                      :icon="capabilityIcon(capability)"
                    >
                      {{ modelCapabilityLabel(capability) }}
                    </q-chip>
                    <q-chip v-if="!isModelPresent(model)" dense color="warning" text-color="dark">Nicht verfügbar</q-chip>
                  </div>
                </q-item-section>
                <q-item-section side class="provider-model-actions">
                  <q-toggle
                    :model-value="model.enabled"
                    color="primary"
                    label="Aktiv"
                    :aria-label="`${model.name || model.modelId}: Modell aktiv`"
                    :disable="personalBlocked || !providers.enabled || !provider.enabled || !isModelPresent(model) || modelToggleLoading[model.id]"
                    @update:model-value="toggleModel(model, $event)"
                  />
                  <q-btn flat round dense icon="more_vert" :aria-label="`Aktionen für ${model.name || model.modelId}`">
                    <q-menu class="app-menu-popup app-action-menu">
                      <q-list dense style="min-width: 210px">
                        <q-item
                          v-if="modelSupportsCapability(model, 'chat')"
                          v-close-popup
                          clickable
                          :disable="personalBlocked || !canTestModel(model, provider)"
                          @click="testModel(model)"
                        >
                          <q-item-section avatar><q-icon name="network_check" /></q-item-section>
                          <q-item-section>Testen</q-item-section>
                        </q-item>
                        <q-item v-close-popup clickable :disable="personalBlocked" @click="openModelDialog(provider, model)">
                          <q-item-section avatar><q-icon name="tune" /></q-item-section>
                          <q-item-section>{{ modelSupportsCapability(model, 'chat') ? 'Laufzeit bearbeiten' : 'Modell bearbeiten' }}</q-item-section>
                        </q-item>
                        <template v-if="model.origin === 'manual'">
                          <q-separator />
                          <q-item v-close-popup clickable :disable="personalBlocked" class="text-negative" @click="removeModel(model)">
                            <q-item-section avatar><q-icon name="delete" /></q-item-section>
                            <q-item-section>Entfernen</q-item-section>
                          </q-item>
                        </template>
                      </q-list>
                    </q-menu>
                  </q-btn>
                </q-item-section>
              </q-item>
              <q-item v-if="!filteredModels(provider).length">
                <q-item-section class="text-center app-muted q-py-lg">Keine passenden Modelle gefunden.</q-item-section>
              </q-item>
            </q-list>
          </q-card-section>
        </q-expansion-item>
      </q-card>
    </div>

    <q-inner-loading :showing="providers.loading && !llmProviders.length" />
    <ProviderConfigDialog
      v-model="dialogOpen"
      :provider="selectedProvider"
      @provider-created="handleProviderCreated"
      @provider-updated="handleProviderUpdated"
    />
    <ModelConfigDialog
      v-model="modelDialogOpen"
      :provider="selectedModelProvider"
      :model="selectedModel"
    />
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import ModelConfigDialog from 'components/ModelConfigDialog.vue'
import ProviderConfigDialog from 'components/ProviderConfigDialog.vue'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import {
  discoveryState,
  isModelEffective,
  isModelPresent,
  isProviderEffective,
  MODEL_CAPABILITY_OPTIONS,
  modelCapabilityLabel,
  modelSupportsCapability,
  normalizeModelCapabilities
} from 'src/utils/provider-models'
import { useProviderStore } from 'stores/providers'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'ProviderSettingsPage' })

const $q = useQuasar()
const { confirm } = useConfirm()
const providers = useProviderStore()
const auth = useAuthStore()
const dialogOpen = ref(false)
const modelDialogOpen = ref(false)
const selectedProvider = ref(null)
const selectedModelProvider = ref(null)
const selectedModel = ref(null)
const testingProviderId = ref(null)
const runtimeToggleLoading = ref(false)
const gateLoading = ref(false)
const expandedProviders = reactive({})
const syncing = reactive({})
const syncFailed = reactive({})
const providerToggleLoading = reactive({})
const modelToggleLoading = reactive({})
const modelSearch = reactive({})
const llmProviders = computed(() => providers.providers.filter((item) => (
  ['openai-codex', 'openai-compatible', 'lemonade'].includes(item.adapterType)
)))
const personalBlocked = computed(() => (
  providers.scope === 'personal' && !providers.personalProvidersEnabled
))
const scopeBusy = computed(() => providers.loading
  || gateLoading.value
  || runtimeToggleLoading.value
  || Object.values(syncing).some(Boolean)
  || Object.values(providerToggleLoading).some(Boolean)
  || Object.values(modelToggleLoading).some(Boolean))

async function changeScope(scope) {
  dialogOpen.value = false
  modelDialogOpen.value = false
  providers.selectScope(scope)
  await providers.fetchAll().catch(() => {})
}

async function togglePersonalProviders(enabled) {
  gateLoading.value = true
  try {
    await providers.setPersonalProvidersEnabled(enabled)
  } finally {
    gateLoading.value = false
  }
}

function openCreateDialog() {
  selectedProvider.value = null
  dialogOpen.value = true
}

function openEditDialog(provider) {
  selectedProvider.value = provider
  dialogOpen.value = true
}

function openModelDialog(provider, model = null) {
  selectedModelProvider.value = provider
  selectedModel.value = model
  modelDialogOpen.value = true
}

function providerModels(provider) {
  return providers.models.filter((model) => model.providerId === provider.id)
}

function filteredModels(provider) {
  const query = (modelSearch[provider.id] || '').trim().toLocaleLowerCase('de')
  if (!query) return providerModels(provider)
  return providerModels(provider).filter((model) => (
    `${model.name || ''} ${model.modelId}`.toLocaleLowerCase('de').includes(query)
  ))
}

function activeModelCount(provider) {
  return providerModels(provider).filter((model) => (
    isModelEffective(model, provider, providers.codex, providers.enabled)
  )).length
}

function providerIcon(provider) {
  if (provider.adapterType === 'openai-codex') return 'key'
  if (provider.adapterType === 'lemonade') return 'developer_board'
  return 'hub'
}

function providerStatus(provider) {
  if (!providers.enabled) return { label: 'Laufzeit deaktiviert', color: 'grey-7' }
  if (!provider.enabled) return { label: 'Deaktiviert', color: 'grey-7' }
  if (provider.adapterType === 'openai-codex' && !providers.codex.authenticated) {
    return { label: 'Nicht verbunden', color: 'warning' }
  }
  return { label: 'Aktiv', color: 'positive' }
}

function providerDescription(provider) {
  if (provider.adapterType === 'openai-codex') return 'ChatGPT-Abonnement · Codex OAuth'
  if (provider.adapterType === 'lemonade') {
    return `${provider.config?.baseUrl || provider.config?.base_url || 'Keine URL'} · Lemonade`
  }
  const apiType = provider.config?.apiType || provider.config?.api_type
  const protocol = apiType === 'responses' ? 'Responses API' : 'Chat Completions'
  return `${provider.config?.baseUrl || provider.config?.base_url || 'Keine URL'} · ${protocol}`
}

function canTest(provider) {
  return providerModels(provider).some((model) => (
    modelSupportsCapability(model, 'chat')
    && isModelEffective(model, provider, providers.codex, providers.enabled)
  ))
}

function canTestModel(model, provider) {
  return providers.enabled
    && isModelPresent(model)
    && isProviderEffective(provider, providers.codex)
    && modelSupportsCapability(model, 'chat')
}

function supportsDiscovery(provider) {
  return provider.adapterType === 'openai-compatible' || provider.adapterType === 'lemonade'
    || (provider.adapterType === 'openai-codex' && providers.codex.authenticated === true)
}

function capabilityIcon(capability) {
  return MODEL_CAPABILITY_OPTIONS.find((option) => option.value === capability)?.icon || 'extension'
}

function modelIcon(model) {
  const capability = normalizeModelCapabilities(model.capabilities).find((item) => item !== 'chat') || 'chat'
  return capabilityIcon(capability)
}

function showProminentManualAction(provider) {
  return discoveryState(provider, syncing[provider.id], syncFailed[provider.id]) !== 'synced'
    || providerModels(provider).length === 0
}

function formatDate(value) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('de', {
    dateStyle: 'medium',
    timeStyle: 'short'
  }).format(date)
}

async function handleProviderCreated(id) {
  expandedProviders[id] = true
  const provider = providers.providers.find((item) => item.id === id)
  if (provider) await syncProviderModels(provider)
}

async function handleProviderUpdated(id) {
  expandedProviders[id] = true
  const provider = providers.providers.find((item) => item.id === id)
  if (provider && supportsDiscovery(provider)) await syncProviderModels(provider)
}

async function toggleRuntime(enabled) {
  runtimeToggleLoading.value = true
  try {
    await providers.setAiEnabled(enabled)
  } catch {
    await providers.fetchAll().catch(() => {})
  } finally {
    runtimeToggleLoading.value = false
  }
}

async function toggleProvider(provider, enabled) {
  providerToggleLoading[provider.id] = true
  try {
    await providers.setProviderEnabled(provider.id, enabled)
  } catch {
    await providers.fetchAll().catch(() => {})
  } finally {
    providerToggleLoading[provider.id] = false
  }
}

async function toggleModel(model, enabled) {
  modelToggleLoading[model.id] = true
  try {
    await providers.setModelEnabled(model.id, enabled)
  } catch {
    await providers.fetchAll().catch(() => {})
  } finally {
    modelToggleLoading[model.id] = false
  }
}

async function syncProviderModels(provider) {
  if (!supportsDiscovery(provider)) return
  syncing[provider.id] = true
  syncFailed[provider.id] = false
  try {
    await providers.syncModels(provider.id)
  } catch {
    syncFailed[provider.id] = true
  } finally {
    syncing[provider.id] = false
  }
}

async function testProvider(id) {
  testingProviderId.value = id
  try {
    await providers.testProvider(id)
    $q.notify({ type: 'positive', message: 'Verbindung erfolgreich' })
  } finally {
    testingProviderId.value = null
  }
}

async function testModel(model) {
  await providers.testModel(model.id)
  $q.notify({ type: 'positive', message: 'Modell erfolgreich getestet' })
}

async function removeModel(model) {
  const confirmed = await confirm(
    `Das manuelle Modell „${model.name || model.modelId}“ entfernen?`,
    'Modell entfernen',
    { okLabel: 'Entfernen' }
  )
  if (!confirmed) return
  await providers.removeModel(model.id)
  $q.notify({ type: 'positive', message: 'Modell entfernt' })
}

async function removeProvider(provider) {
  const confirmed = await confirm(
    `Provider „${provider.name}“ einschließlich aller Modelle und Zugangsdaten löschen?`,
    'Provider löschen',
    { okLabel: 'Löschen' }
  )
  if (!confirmed) return
  await providers.removeProvider(provider.id)
  $q.notify({ type: 'positive', message: 'Provider gelöscht' })
}

onMounted(() => changeScope(auth.isAdmin ? 'instance' : 'personal'))
</script>
