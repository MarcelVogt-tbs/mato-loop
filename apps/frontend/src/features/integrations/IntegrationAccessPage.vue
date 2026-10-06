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
        :to="{ name: listRoute }"
      />
      <q-avatar color="primary" text-color="white" icon="folder_open" class="q-ml-sm" />
      <div class="col q-px-sm">
        <div class="text-h6 text-weight-bold">
          {{ permissionsData ? `Berechtigungen für ${permissionsData.name}` : 'Integrationsrechte' }}
        </div>
        <div class="text-body2 app-muted">
          Rechte dieser Integration und gegebenenfalls Standardressourcen festlegen.
        </div>
      </div>
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
      Die Berechtigungen und Ressourcen konnten nicht geladen werden.
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
      <AppSelect dense v-if="scope === 'personal'" :model-value="profileId" outlined emit-value map-options label="Agentenprofil" :options="profileOptions" :disable="saving || loading" @update:model-value="selectProfile" />
      <AppSelect dense v-if="profileId" v-model="permissions.available" outlined emit-value map-options label="Integration für dieses Profil" :options="[{ label: `Erben: ${permissionsData?.inheritedAvailable ? 'Verfügbar' : 'Nicht verfügbar'}`, value: null }, { label: 'Verfügbar', value: true }, { label: 'Nicht verfügbar', value: false }]" :disable="saving || loading" />
      <q-banner rounded class="app-surface-tonal">
        {{ profileId ? 'Diese Abweichungen gelten nur für diese persönliche Integration und das gewählte Profil.' : 'Diese Integration hat eigene Berechtigungen, unabhängig von globalen Tool-Defaults.' }}
      </q-banner>
      <PermissionFields v-model="permissions" :groups-by-trust="permissionGroups" :resources="permissionsData?.inherited.private?.resources || []" :inherited-resources="inheritedResources" :disabled="adapterBlocked" />
      <q-banner v-if="!permissionGroups.private?.[0]?.actions.length" rounded class="app-surface-tonal">Diese Integration bietet keine konfigurierbaren Ausführungsrechte.</q-banner>
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
              <AppSelect
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
              <AppSelect
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
           :to="{ name: listRoute }"
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
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import PermissionFields from 'src/features/settings/PermissionFields.vue'
import {
  calendarResources,
  taskListResources
} from 'src/utils/integration-access'
import { useIntegrationStore } from 'stores/integrations'

defineOptions({ name: 'IntegrationAccessPage' })

const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const access = ref(null)
const permissionsData = ref(null)
const permissions = ref({ privatePermissions: { policies: {}, resourcePolicies: {} }, publicPermissions: { policies: {}, resourcePolicies: {} } })
const profileId = ref(null)
const savedPermissions = ref('')
const listRoute = computed(() => `${String(route.name).startsWith('admin-') ? 'admin' : 'settings'}-${String(route.name).includes('mcp') ? 'mcp' : 'integrations'}`)
const profileOptions = computed(() => [{ label: 'Standard für alle Profile', value: null }, ...(permissionsData.value?.profiles || []).map((item) => ({ label: `${item.name} (${item.scope === 'instance' ? 'Global' : 'Persönlich'})`, value: item.id }))])
const permissionGroups = computed(() => Object.fromEntries(['private', 'public'].map((trust) => [trust, [{ id: 'integration', label: 'Berechtigungen', icon: 'security', tools: [], actions: (permissionsData.value?.inherited[trust]?.actions || []).map((action) => ({ ...action, available: !profileId.value || (permissions.value.available ?? permissionsData.value?.inheritedAvailable) })) }]])))
const inheritedResources = computed(() => Object.fromEntries(['private', 'public'].map((trust) => [trust, permissionsData.value?.inherited[trust]?.resources || []])))
const defaultResourceKey = ref(null)
const defaultTaskListKey = ref(null)
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const savedPayload = ref('')
let loadToken = 0

const integrationId = computed(() => String(route.params.integrationId || route.params.serverId || ''))
const scope = computed(() => String(route.name).startsWith('admin-') ? 'instance' : 'personal')
const adapterBlocked = computed(() => (
  !permissionsData.value || (permissionsData.value.providerType !== 'mcp_streamable_http' && !integrations.adapterAvailable(permissionsData.value.providerType, scope.value))
))
const dirty = computed(() => Boolean(
    access.value && (JSON.stringify(requestPayload()) !== savedPayload.value || JSON.stringify(permissions.value) !== savedPermissions.value)
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
    await integrations.fetchCatalog()
    const rights = await integrations.fetchPermissions(id, profileId.value)
    if (token !== loadToken || integrationId.value !== id) return
    permissionsData.value = rights
    permissions.value = rights.permissions
    savedPermissions.value = JSON.stringify(permissions.value)
    const value = rights.providerType === 'caldav' ? await integrations.fetchAccess(id) : {}
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
    if (JSON.stringify(permissions.value) !== savedPermissions.value) {
      await integrations.savePermissions(id, permissions.value, profileId.value)
      savedPermissions.value = JSON.stringify(permissions.value)
    }
    if (JSON.stringify(payload) !== savedPayload.value) assignAccess(await integrations.saveAccess(id, payload))
    $q.notify({ type: 'positive', message: 'Integrationseinstellungen gespeichert' })
  } catch { /* Central API toast. */ } finally {
    saving.value = false
  }
}

async function selectProfile(id) {
  if (!await confirmDiscardedChanges()) return
  profileId.value = id
  await load()
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
