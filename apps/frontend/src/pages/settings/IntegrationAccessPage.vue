<template>
  <section :aria-busy="loading">
    <q-toolbar class="q-px-none q-mb-md">
      <q-btn
        flat
        round
        dense
        icon="arrow_back"
        aria-label="Zurück zu den Integrationen"
        :disable="saving"
        :to="{ name: 'settings-integrations', query: { scope } }"
      />
      <q-avatar color="primary" text-color="white" icon="policy" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="text-h6 text-weight-bold">
          {{ integration ? `Zugriff für ${integration.name}` : 'Zugriff verwalten' }}
        </div>
        <div class="text-body2 app-muted">
          {{ accountPoliciesOnly
            ? 'Festlegen, wie der Agent dieses Mail-Konto verwenden darf.'
            : 'Standardwerte und Abweichungen für einzelne Ressourcen festlegen.' }}
        </div>
      </q-toolbar-title>
      <q-btn
        unelevated
        no-caps
        color="primary"
        icon="save"
        :round="$q.screen.lt.sm"
        :label="$q.screen.gt.xs ? 'Speichern' : undefined"
        aria-label="Zugriffsrichtlinien speichern"
        :loading="saving"
        :disable="adapterBlocked || loading || loadFailed || !access || !dirty"
        @click="save"
      />
    </q-toolbar>

    <div v-if="auth.isAdmin" class="row justify-end q-mb-md">
      <SettingsScopeToggle
        :model-value="scope"
        :is-admin="auth.isAdmin"
        :disable="loading || saving"
        @update:model-value="switchScope"
      />
    </div>

    <q-banner v-if="scope === 'personal'" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="person" color="primary" /></template>
      Die Verfügbarkeit wird je Integrationstyp festgelegt. Ist dieser Typ nicht persönlich verfügbar, bleiben gespeicherte Zugriffsregeln erhalten, können aber nicht bearbeitet werden.
    </q-banner>

    <q-banner v-if="loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Zugriffsrichtlinien konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="primary" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <div v-if="loading" class="q-gutter-md" aria-label="Zugriffsrichtlinien werden geladen">
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
            Gilt {{ scope === 'instance' ? 'instanzweit' : 'persönlich' }} für unspezifische Kalender- und Aufgabenanfragen sowie neue
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

      <q-card flat class="app-surface-tonal">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">
            {{ accountPoliciesOnly ? 'Richtlinien für dieses Konto' : 'Standard für Integration' }}
          </div>
          <div class="text-caption app-muted">
            {{ accountPoliciesOnly
              ? 'Diese Richtlinien gelten für alle IMAP-Ordner des Kontos.'
              : 'Gilt für Ressourcen ohne eigene Einstellung.' }}
          </div>
        </q-card-section>
        <q-separator />
        <q-card-section>
          <div class="row q-col-gutter-md">
            <div v-for="action in access.actions" :key="action.capability" class="col-12 col-md-4">
              <q-select
                v-model="integrationForm[action.capability]"
                outlined
                dense
                emit-value
                map-options
                :label="actionLabel(action)"
                :options="policyOptions(action, action.globalPolicy)"
                :hint="`Wirksam: ${policyLabel(action.effectiveIntegrationPolicy)}`"
                :disable="adapterBlocked || action.globalPolicy === 'disabled'"
                persistent-hint
              />
            </div>
          </div>
        </q-card-section>
      </q-card>

      <q-banner v-if="!accountPoliciesOnly && !access.resources.length" rounded class="app-surface-tonal">
        Diese Integration stellt derzeit keine konfigurierbaren Ressourcen bereit.
      </q-banner>

      <q-card
        v-for="group in resourceGroups"
        :key="group[0].key"
        flat
        class="app-surface-tonal"
      >
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">{{ group[0].label }}</div>
        </q-card-section>
        <template v-for="resource in group" :key="resource.key">
          <q-separator />
          <q-card-section>
            <div class="text-subtitle2 text-weight-bold q-mb-sm">
              {{ resourceDomainLabel(resource) }}
            </div>
            <div class="row q-col-gutter-md">
              <div
                v-for="action in applicableResourceActions(access.actions, resource)"
                :key="action.capability"
                class="col-12 col-md-4"
              >
                <q-select
                  v-model="resourceForm[resource.key][action.capability]"
                  outlined
                  dense
                  emit-value
                  map-options
                  :label="actionLabel(action)"
                  :options="policyOptions(action, action.effectiveIntegrationPolicy)"
                  :hint="effectiveHint(resource, action)"
                  :disable="adapterBlocked || action.globalPolicy === 'disabled' || resource.hardUnavailableCapabilities.includes(action.capability)"
                  persistent-hint
                />
              </div>
            </div>
          </q-card-section>
        </template>
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
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import {
  applicableResourceActions,
  calendarResources,
  groupResources,
  taskListResources
} from 'src/utils/integration-access'
import { useIntegrationStore } from 'stores/integrations'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'IntegrationAccessPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const auth = useAuthStore()
const access = ref(null)
const defaultResourceKey = ref(null)
const defaultTaskListKey = ref(null)
const integrationForm = reactive({})
const resourceForm = reactive({})
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
  ...integrations.knowledge.bases
].find((item) => item.id === integrationId.value))
const adapterBlocked = computed(() => (
  !integration.value || !integrations.adapterAvailable(integration.value.providerType, scope.value)
))
const accountPoliciesOnly = computed(() => integration.value?.integrationType === 'mail')
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
const resourceGroups = computed(() => groupResources(access.value?.resources || []))
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

function policyLabel(policy) {
  return {
    disabled: 'Nicht erlaubt',
    ask: 'Nachfragen',
    auto: 'Automatisch'
  }[policy] || policy
}

function actionLabel(action) {
  return {
    'mail.read': 'E-Mails lesen',
    'mail.send': 'E-Mails senden',
    'mail.drafts': 'E-Mail-Entwürfe verwalten',
    'mail.delete': 'E-Mails löschen'
  }[action.capability] || action.label
}

function policyOptions(action, inheritedPolicy) {
  return [
    { label: `Erben: ${policyLabel(inheritedPolicy)}`, value: 'inherit' },
    ...action.allowedPolicies
      .filter((policy) => policy !== 'inherit')
      .map((policy) => ({ label: policyLabel(policy), value: policy }))
  ]
}

function effectiveHint(resource, action) {
  const unavailable = resource.hardUnavailableCapabilities.includes(action.capability)
  if (unavailable) return 'Vom Dienst nicht erlaubt'
  if (action.globalPolicy === 'disabled') return 'Global nicht erlaubt'
  return `Wirksam: ${policyLabel(resource.effectivePolicies[action.capability])}`
}

function resourceDomainLabel(resource) {
  if (resource.key.startsWith('calendar:')) return 'Kalender'
  if (resource.key.startsWith('tasklist:')) return 'Aufgaben'
  if (resource.key.startsWith('book:')) return 'Buch'
  return 'Ressource'
}

function requestPayload() {
  const policies = Object.fromEntries(
    Object.entries(integrationForm).filter(([, policy]) => policy !== 'inherit')
  )
  const resources = {}
  for (const resource of access.value?.resources || []) {
    const resourcePolicies = Object.fromEntries(
      Object.entries(resourceForm[resource.key]).filter(([, policy]) => policy !== 'inherit')
    )
    if (Object.keys(resourcePolicies).length) resources[resource.key] = resourcePolicies
  }
  return {
    policies,
    resources,
    ...(hasDefaultResourceFields.value
      ? {
          defaultResourceKey: defaultResourceKey.value,
          defaultTaskListKey: defaultTaskListKey.value
        }
      : {})
  }
}

function assignAccess(value) {
  defaultResourceKey.value = value.defaultResourceKey ?? null
  defaultTaskListKey.value = value.defaultTaskListKey ?? null
  for (const key of Object.keys(integrationForm)) delete integrationForm[key]
  for (const key of Object.keys(resourceForm)) delete resourceForm[key]
  for (const action of value.actions) {
    integrationForm[action.capability] = action.integrationPolicy || 'inherit'
  }
  for (const resource of value.resources) {
    resourceForm[resource.key] = {}
    for (const action of applicableResourceActions(value.actions, resource)) {
      resourceForm[resource.key][action.capability] = resource.policies[action.capability] || 'inherit'
    }
  }
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

async function switchScope(value) {
  const failure = await router.push({ name: 'settings-integrations', query: { scope: value } })
  if (failure) return
  integrations.selectScope(value)
  await integrations.fetchAll().catch(() => {})
}

async function save() {
  if (!access.value || !dirty.value) return
  const id = integrationId.value
  const payload = requestPayload()
  saving.value = true
  try {
    const value = await integrations.saveAccess(id, payload)
    if (integrationId.value !== id) return
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
