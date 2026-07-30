<template>
  <section>
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
        <q-item v-if="integrations.loaded && !searchProviders.length && !integrations.loading" class="q-py-lg">
          <q-item-section avatar>
            <q-avatar color="grey-7" text-color="white" icon="extension" />
          </q-item-section>
          <q-item-section>
            <q-item-label>Noch keine Integration konfiguriert</q-item-label>
            <q-item-label caption>Füge einen externen Dienst wie SearXNG hinzu.</q-item-label>
          </q-item-section>
        </q-item>

        <q-item v-for="integration in searchProviders" :key="integration.id" class="integration-list-item">
          <q-item-section avatar top>
            <q-avatar color="primary" text-color="white" icon="travel_explore" />
          </q-item-section>
          <q-item-section>
            <div class="row items-center q-gutter-sm">
              <q-item-label class="text-weight-medium">{{ integration.name }}</q-item-label>
              <q-chip dense :color="integration.enabled ? 'positive' : 'grey-7'" text-color="white">
                {{ integration.enabled ? 'Aktiv' : 'Deaktiviert' }}
              </q-chip>
              <q-chip v-if="integration.enabled" dense outline color="primary">Im Tool verfügbar</q-chip>
            </div>
            <q-item-label caption>Websuche · {{ providerLabel(integration.providerType) }}</q-item-label>
            <q-item-label caption>{{ integration.config.baseUrl }}</q-item-label>
          </q-item-section>
          <q-item-section side class="integration-list-actions">
            <q-btn
              v-if="integration.supportsResourcePolicies"
              flat
              dense
              no-caps
              icon="policy"
              label="Zugriff"
              @click="openAccessDialog(integration)"
            />
            <q-btn
              flat
              dense
              no-caps
              icon="network_check"
              label="Testen"
              :loading="testingId === integration.id"
              @click="testIntegration(integration.id)"
            />
            <q-btn
              flat
              dense
              no-caps
              icon="edit"
              label="Bearbeiten"
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
          </q-item-section>
        </q-item>
      </q-list>
      <q-inner-loading :showing="integrations.loading" />
    </q-card>

    <IntegrationConfigDialog v-model="dialogOpen" :integration="selectedIntegration" />
    <IntegrationAccessDialog v-model="accessDialogOpen" :integration="accessIntegration" />
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import IntegrationConfigDialog from 'components/IntegrationConfigDialog.vue'
import IntegrationAccessDialog from 'components/IntegrationAccessDialog.vue'
import { useConfirm } from 'src/composables/confirm'
import { useIntegrationStore } from 'stores/integrations'

defineOptions({ name: 'IntegrationSettingsPage' })

const $q = useQuasar()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const dialogOpen = ref(false)
const selectedIntegration = ref(null)
const accessDialogOpen = ref(false)
const accessIntegration = ref(null)
const testingId = ref(null)
const searchProviders = computed(() => integrations.search.providers)

function openCreateDialog() {
  selectedIntegration.value = null
  dialogOpen.value = true
}

function openEditDialog(integration) {
  selectedIntegration.value = integration
  dialogOpen.value = true
}

function openAccessDialog(integration) {
  accessIntegration.value = integration
  accessDialogOpen.value = true
}

function providerLabel(providerType) {
  return providerType === 'searxng' ? 'SearXNG' : providerType
}

async function testIntegration(id) {
  testingId.value = id
  try {
    await integrations.testSearch(id)
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
  await integrations.removeSearch(integration.id)
  $q.notify({ type: 'positive', message: 'Integration gelöscht' })
}

async function reload() {
  try {
    await integrations.fetchSearch()
  } catch {
    // The central API interceptor displays the error as a toast.
  }
}

onMounted(reload)
</script>
