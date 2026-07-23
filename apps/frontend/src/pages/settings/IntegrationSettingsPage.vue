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

    <q-card flat class="app-surface integration-list-card">
      <q-list separator>
        <q-item v-if="!searchProviders.length && !integrations.loading" class="q-py-lg">
          <q-item-section avatar>
            <q-avatar color="grey-7" text-color="white" icon="extension" />
          </q-item-section>
          <q-item-section>
            <q-item-label>Noch keine Integration konfiguriert</q-item-label>
            <q-item-label caption>Füge eine Websuche mit einem unterstützten Anbieter hinzu.</q-item-label>
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
              <q-chip v-if="isDefault(integration)" dense outline color="primary">Standard</q-chip>
            </div>
            <q-item-label caption>Websuche · {{ providerLabel(integration.providerType) }}</q-item-label>
            <q-item-label caption>{{ integration.config.baseUrl }}</q-item-label>
          </q-item-section>
          <q-item-section side class="integration-list-actions">
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
              v-if="integration.enabled && !isDefault(integration)"
              flat
              dense
              no-caps
              icon="star"
              label="Als Standard"
              @click="setDefault(integration.id)"
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
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import IntegrationConfigDialog from 'components/IntegrationConfigDialog.vue'
import { useConfirm } from 'src/composables/confirm'
import { useIntegrationStore } from 'stores/integrations'

defineOptions({ name: 'IntegrationSettingsPage' })

const $q = useQuasar()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const dialogOpen = ref(false)
const selectedIntegration = ref(null)
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

function providerLabel(providerType) {
  return providerType === 'searxng' ? 'SearXNG' : providerType
}

function isDefault(integration) {
  return integrations.search.defaultIntegrationId === integration.id
}

async function testIntegration(id) {
  testingId.value = id
  try {
    await integrations.testSearch(id)
    $q.notify({ type: 'positive', message: 'Verbindung erfolgreich' })
  } catch {
    // Der zentrale API-Interceptor zeigt den Fehler als Toast an.
  } finally {
    testingId.value = null
  }
}

async function setDefault(id) {
  await integrations.setDefaultSearch(id)
  $q.notify({ type: 'positive', message: 'Standardintegration geändert' })
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

onMounted(() => integrations.fetchSearch())
</script>
