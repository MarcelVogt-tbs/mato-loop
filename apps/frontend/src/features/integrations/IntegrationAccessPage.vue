<template>
  <section :aria-busy="loading">
    <q-toolbar class="settings-detail-toolbar q-px-none q-mb-md">
      <q-btn
        flat
        round
        dense
        icon="arrow_back"
        aria-label="Zurück zu den Integrationen"
        :disable="saving"
        :to="{ name: 'settings-integrations', query: { scope } }"
      />
      <q-avatar color="primary" text-color="white" icon="folder_open" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="text-h6 text-weight-bold">
          {{ integration ? `Standardressourcen für ${integration.name}` : 'Standardressourcen' }}
        </div>
        <div class="text-body2 app-muted">
          Standardkalender und Standard-Aufgabenliste festlegen. Rechte werden in Agentenprofilen verwaltet.
        </div>
      </q-toolbar-title>
      <div class="settings-scope-actions row items-center col-auto">
        <q-btn
          class="col-auto"
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="save"
          label="Speichern"
          aria-label="Standardressourcen speichern"
          :loading="saving"
          :disable="adapterBlocked || loading || loadFailed || !access || !dirty"
          @click="save"
        />
      </div>
    </q-toolbar>

    <q-banner v-if="loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Standardressourcen konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="primary" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <div v-if="loading" class="q-gutter-md" aria-label="Standardressourcen werden geladen">
      <q-card v-for="card in 3" :key="card" flat class="app-surface-tonal">
        <q-card-section>
          <q-skeleton type="text" width="35%" />
          <q-skeleton type="text" width="55%" />
        </q-card-section>
        <q-separator />
        <q-card-section>
          <div class="row q-col-gutter-md">
            <div v-for="field in 3" :key="field" class="col-12 col-md-4">
              <q-skeleton type="QInput" />
            </div>
          </div>
        </q-card-section>
      </q-card>
    </div>

    <q-form
      v-else-if="access"
      class="q-gutter-md"
      :class="{ disabled: adapterBlocked }"
      :inert="adapterBlocked"
      @submit.prevent="save"
    >
      <q-card v-if="supportsDefaultResources" flat class="app-surface-tonal">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Standardressourcen dieses Kontos</div>
          <div class="text-caption app-muted">
            Gilt {{ scope === 'instance' ? 'global' : 'persönlich' }} für unspezifische Kalender- und Aufgabenanfragen sowie neue
            Einträge. Beide Standards können unabhängig festgelegt werden.
          </div>
        </q-card-section>
        <q-separator />
        <q-card-section>
          <div class="row q-col-gutter-md">
            <div v-if="calendarResourceList.length" class="col-12 col-md-6">
              <q-select
                v-model="defaultResourceKey"
                outlined
                dense
                emit-value
                map-options
                label="Standardkalender"
                :options="defaultCalendarOptions"
                :disable="adapterBlocked"
              />
            </div>
            <div v-if="taskListResourceList.length" class="col-12 col-md-6">
              <q-select
                v-model="defaultTaskListKey"
                outlined
                dense
                emit-value
                map-options
                label="Standard-Aufgabenliste"
                :options="defaultTaskListOptions"
                :disable="adapterBlocked"
              />
            </div>
          </div>
        </q-card-section>
      </q-card>

      <div class="row justify-end q-gutter-sm q-pb-md">
        <q-btn
          flat
          no-caps
          label="Zurück"
          :disable="saving"
          :to="{ name: 'settings-integrations', query: { scope } }"
        />
        <q-btn
          unelevated
          no-caps
          type="submit"
          color="primary"
          icon="save"
          label="Speichern"
          :loading="saving"
          :disable="adapterBlocked || !dirty"
        />
      </div>
    </q-form>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import {
  calendarResources,
  taskListResources
} from 'src/utils/integration-access'
import { useIntegrationStore } from 'stores/integrations'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'IntegrationAccessPage' })

const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const auth = useAuthStore()
const access = ref(null)
const defaultResourceKey = ref(null)
const defaultTaskListKey = ref(null)
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const savedPayload = ref('')
let loadToken = 0

const integrationId = computed(() => String(route.params.integrationId || ''))
const scope = computed(() => resolveSettingsScope(route.query.scope, auth.isAdmin))
const integration = computed(() => [
  ...integrations.search.providers,
  ...integrations.calendar.accounts,
  ...integrations.mail.accounts,
  ...integrations.knowledge.bases,
  ...integrations.music.instances,
  ...integrations.browser.instances.map((item) => ({ ...item, integrationType: 'browser', providerType: 'browser' }))
].find((item) => item.id === integrationId.value))
const adapterBlocked = computed(() => (
  !integration.value || !integrations.adapterAvailable(integration.value.providerType, scope.value)
))
const dirty = computed(() => Boolean(
  access.value && JSON.stringify(requestPayload()) !== savedPayload.value
))
const calendarResourceList = computed(() => calendarResources(access.value?.resources || []))
const taskListResourceList = computed(() => taskListResources(access.value?.resources || []))
const hasDefaultResourceFields = computed(() => Boolean(
  access.value
    && 'defaultResourceKey' in access.value
    && 'defaultTaskListKey' in access.value
))
const supportsDefaultResources = computed(() => Boolean(
  hasDefaultResourceFields.value
    && (calendarResourceList.value.length || taskListResourceList.value.length)
))
const defaultCalendarOptions = computed(() => [
  { label: 'Keiner in diesem Konto', value: null },
  ...calendarResourceList.value.map((resource) => ({
    label: resource.label,
    value: resource.key
  }))
])
const defaultTaskListOptions = computed(() => [
  { label: 'Keine in diesem Konto', value: null },
  ...taskListResourceList.value.map((resource) => ({
    label: resource.label,
    value: resource.key
  }))
])

function requestPayload() {
  return hasDefaultResourceFields.value
    ? {
        defaultResourceKey: defaultResourceKey.value,
        defaultTaskListKey: defaultTaskListKey.value
      }
    : {}
}

function assignAccess(value) {
  defaultResourceKey.value = value.defaultResourceKey ?? null
  defaultTaskListKey.value = value.defaultTaskListKey ?? null
  access.value = value
  savedPayload.value = JSON.stringify(requestPayload())
}

async function load() {
  const token = ++loadToken
  const id = integrationId.value
  access.value = null
  savedPayload.value = ''
  loading.value = true
  loadFailed.value = false
  try {
    integrations.selectScope(scope.value)
    await Promise.all([integrations.fetchCatalog(), integrations.fetchAll()])
    if (adapterBlocked.value) return
    const value = await integrations.fetchAccess(id)
    if (token === loadToken && integrationId.value === id) assignAccess(value)
  } catch {
    if (token === loadToken) loadFailed.value = true
  } finally {
    if (token === loadToken) loading.value = false
  }
}

async function save() {
  if (!access.value || !dirty.value) return
  const id = integrationId.value
  const payload = requestPayload()
  saving.value = true
  try {
    const value = await integrations.saveAccess(id, payload)
    if (integrationId.value !== id || !value) return
    assignAccess(value)
    $q.notify({ type: 'positive', message: 'Integrationseinstellungen gespeichert' })
  } finally {
    saving.value = false
  }
}

async function confirmDiscardedChanges() {
  if (saving.value) return false
  if (!dirty.value) return true
  return confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
}

watch([integrationId, scope], load, { immediate: true })
onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
