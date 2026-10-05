<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Kanäle</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
           {{ admin ? 'Kommunikationskanäle aktivieren und verfügbare Anbieter verwalten.' : 'Persönliche Nachrichtenkanäle für Chats und Automationen verwalten.' }}
        </p>
      </div>
      <q-btn
        v-if="showInstances"
        color="primary"
        unelevated
        no-caps
        no-wrap
        icon="add"
        label="Kanal hinzufügen"
        :disable="channels.loading || !canCreateChannel"
        @click="openCreate"
      />
    </header>

    <q-banner v-if="channels.loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Kanäle konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="primary" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <q-tabs v-if="admin" v-model="section" dense no-caps align="left" active-color="primary" indicator-color="primary" class="q-mb-lg">
      <q-tab name="general" icon="settings" label="Einstellungen" />
      <q-tab name="management" icon="forum" label="Verwaltung" />
    </q-tabs>

    <q-card v-if="admin && section === 'general'" flat class="app-surface">
      <q-card-section>
        <q-toggle :model-value="channels.enabled" color="primary" label="Kommunikationskanäle aktivieren" :disable="channels.loading || channels.loadFailed || savingSettings" @update:model-value="setEnabled" />
        <div class="text-caption app-muted q-mt-sm">Aktiviert Empfang und externe Zustellung für freigegebene Anbieter. Gilt für alle Benutzer einschließlich Administratoren.</div>
      </q-card-section>
    </q-card>

    <q-card v-if="admin && section === 'management'" flat class="app-surface">
      <q-card-section v-if="channels.loaded && !channels.enabled" class="text-caption app-muted">Kommunikationskanäle sind global deaktiviert. Die Anbieterfreigaben bleiben gespeichert.</q-card-section>
      <q-list separator>
        <q-item v-for="provider in channels.providers" :key="provider.provider" class="q-py-md">
          <q-item-section avatar>
            <q-avatar color="primary" text-color="white" :icon="providerIcon(provider.provider)" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="text-weight-medium">{{ provider.label }}</q-item-label>
          </q-item-section>
          <q-item-section side>
            <q-toggle
              :model-value="provider.allowed"
              color="primary"
              label="Erlaubt"
              :aria-label="`${provider.label}: erlaubt`"
              :disable="channels.loading || channels.loadFailed || togglingProvider !== null"
              @update:model-value="setProviderAllowed(provider, $event)"
            />
          </q-item-section>
        </q-item>
        <q-item v-if="channels.loaded && !channels.providers.length" class="q-py-lg">
          <q-item-section>Keine Kanalanbieter verfügbar</q-item-section>
        </q-item>
      </q-list>
    </q-card>

    <template v-if="showInstances">
      <q-card v-if="channels.loaded && (!channels.enabled || !allowedProviders.length)" flat class="app-surface q-pa-lg">
        <div class="row items-center no-wrap q-gutter-md">
          <q-avatar color="grey-7" text-color="white" icon="forum" />
          <div>
            <div class="text-weight-medium">{{ channels.enabled ? 'Keine Kanalanbieter freigegeben' : 'Kommunikationskanäle sind global deaktiviert' }}</div>
            <div class="text-body2 app-muted">Vorhandene Kanäle bleiben gespeichert und werden wieder sichtbar, sobald Kommunikationskanäle und ihr Anbieter freigegeben sind.</div>
          </div>
        </div>
      </q-card>

      <q-card v-else flat class="app-surface integration-list-card">
        <q-list separator>
          <q-item v-if="channels.loaded && !visibleEndpoints.length" class="q-py-lg">
            <q-item-section avatar>
              <q-avatar color="grey-7" text-color="white" icon="forum" />
            </q-item-section>
            <q-item-section>
              <q-item-label>Noch keine Kanalinstanz eingerichtet</q-item-label>
              <q-item-label caption>Füge einen Kanal hinzu, um Nachrichten außerhalb der App zu empfangen.</q-item-label>
            </q-item-section>
          </q-item>

          <q-item v-for="endpoint in visibleEndpoints" :key="endpoint.id" class="q-py-md">
            <q-item-section avatar top>
              <q-avatar color="primary" text-color="white" :icon="providerIcon(endpoint.provider)" />
            </q-item-section>
            <q-item-section>
              <div class="row items-center q-gutter-sm">
                <q-item-label class="text-weight-medium">{{ endpoint.name }}</q-item-label>
                <q-chip dense :color="statusColor(endpoint)" :text-color="endpoint.pairingState === 'pending' ? 'black' : 'white'">
                  {{ statusLabel(endpoint) }}
                </q-chip>
              </div>
              <q-item-label caption>
                {{ providerLabel(endpoint.provider) }}<template v-if="endpoint.externalHandle"> · @{{ endpoint.externalHandle }}</template><template v-if="endpoint.pairedDisplayName"> · {{ endpoint.pairedDisplayName }}</template>
              </q-item-label>
            </q-item-section>
            <q-item-section side top>
              <div v-if="endpoint.provider === 'telegram'" class="row items-center no-wrap q-gutter-xs">
                <q-btn
                  flat round dense icon="network_check"
                  :aria-label="`${endpoint.name} testen`"
                  :loading="testingId === endpoint.id"
                  @click="testEndpoint(endpoint)"
                ><q-tooltip>Verbindung testen</q-tooltip></q-btn>
                <q-btn
                  flat round dense color="primary" icon="link"
                  :aria-label="`${endpoint.name} koppeln`"
                  :loading="pairingId === endpoint.id"
                  @click="pairEndpoint(endpoint)"
                ><q-tooltip>Neue Kopplung starten</q-tooltip></q-btn>
                <q-btn flat round dense icon="edit" :aria-label="`${endpoint.name} bearbeiten`" @click="openEdit(endpoint)">
                  <q-tooltip>Bearbeiten</q-tooltip>
                </q-btn>
                <q-btn
                  flat round dense color="negative" icon="delete"
                  :aria-label="`${endpoint.name} löschen`"
                  :loading="removingId === endpoint.id"
                  @click="removeEndpoint(endpoint)"
                ><q-tooltip>Löschen</q-tooltip></q-btn>
              </div>
            </q-item-section>
          </q-item>
        </q-list>
        <q-inner-loading :showing="channels.loading" />
      </q-card>

      <ChannelConfigDialog v-model="dialogOpen" :endpoint="selectedEndpoint" />
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import ChannelConfigDialog from 'src/features/channels/ChannelConfigDialog.vue'
import { useConfirm } from 'src/composables/confirm'
import { useChannelStore } from 'stores/channels'

defineOptions({ name: 'ChannelSettingsPage' })
const { admin } = defineProps({ admin: { type: Boolean, default: false } })

const $q = useQuasar()
const { confirm } = useConfirm()
const channels = useChannelStore()
const dialogOpen = ref(false)
const selectedEndpoint = ref(null)
const togglingProvider = ref(null)
const savingSettings = ref(false)
const section = ref('general')
const testingId = ref(null)
const pairingId = ref(null)
const removingId = ref(null)

const allowedProviders = computed(() => channels.providers.filter((provider) => provider.allowed))
const allowedProviderNames = computed(() => new Set(allowedProviders.value.map((provider) => provider.provider)))
const canCreateChannel = computed(() => channels.enabled && allowedProviders.value.length > 0)
const showInstances = computed(() => !admin)
const visibleEndpoints = computed(() => channels.endpoints.filter(
  (endpoint) => allowedProviderNames.value.has(endpoint.provider)
))

function providerLabel(provider) {
  return channels.providers.find((item) => item.provider === provider)?.label || provider
}

function providerIcon(provider) {
  return provider === 'telegram' ? 'send' : 'forum'
}

function statusLabel(endpoint) {
  if (!endpoint.enabled) return 'Deaktiviert'
  if (endpoint.pairingState === 'pending') return 'Kopplung ausstehend'
  if (endpoint.pairingState !== 'paired') return 'Nicht gekoppelt'
  return endpoint.connected === false ? 'Offline' : 'Verbunden'
}

function statusColor(endpoint) {
  if (!endpoint.enabled || endpoint.pairingState !== 'paired') {
    return endpoint.pairingState === 'pending' ? 'warning' : 'grey-7'
  }
  return endpoint.connected === false ? 'grey-7' : 'positive'
}

function openCreate() {
  selectedEndpoint.value = null
  dialogOpen.value = true
}

function openEdit(endpoint) {
  selectedEndpoint.value = endpoint
  dialogOpen.value = true
}

async function setProviderAllowed(provider, allowed) {
  if (!allowed && !await confirm(
    `${provider.label} sperren? Bereits wartende externe Zustellungen werden beendet. Persönliche Kanäle bleiben gespeichert.`,
    'Kanalanbieter sperren',
    { okLabel: 'Sperren' }
  )) return
  togglingProvider.value = provider.provider
  try {
    await channels.setProviderAllowed(provider.provider, allowed)
    $q.notify({ type: 'positive', message: 'Kanalanbieter gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    togglingProvider.value = null
  }
}

async function setEnabled(enabled) {
  if (!enabled && !await confirm(
    'Kommunikationskanäle global deaktivieren? Bereits wartende externe Zustellungen werden beendet. Persönliche Kanäle und Anbieterfreigaben bleiben gespeichert.',
    'Kommunikationskanäle deaktivieren',
    { okLabel: 'Deaktivieren' }
  )) return
  savingSettings.value = true
  try {
    await channels.setEnabled(enabled)
    $q.notify({ type: 'positive', message: 'Kanaleinstellungen gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    savingSettings.value = false
  }
}

async function testEndpoint(endpoint) {
  testingId.value = endpoint.id
  try {
    await channels.testTelegram(endpoint.id)
    $q.notify({ type: 'positive', message: 'Verbindung erfolgreich' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    testingId.value = null
  }
}

async function pairEndpoint(endpoint) {
  pairingId.value = endpoint.id
  try {
    selectedEndpoint.value = await channels.startTelegramPairing(endpoint.id)
    dialogOpen.value = true
    $q.notify({ type: 'positive', message: 'Neue Telegram-Kopplung gestartet' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    pairingId.value = null
  }
}

async function removeEndpoint(endpoint) {
  const accepted = await confirm(
    `Kanal „${endpoint.name}“ vollständig löschen?`,
    'Kanal löschen',
    { okLabel: 'Löschen' }
  )
  if (!accepted) return
  removingId.value = endpoint.id
  try {
    await channels.removeTelegram(endpoint.id)
    $q.notify({ type: 'positive', message: 'Kanal gelöscht' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    removingId.value = null
  }
}

async function load() {
  try {
    await channels.fetchAll(undefined, !admin)
  } catch {
    // The central Axios interceptor displays the error.
  }
}

onMounted(() => void load())
watch(() => admin, load)
</script>
