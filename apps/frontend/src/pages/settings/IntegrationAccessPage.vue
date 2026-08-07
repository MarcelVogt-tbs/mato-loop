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
        :to="{ name: 'settings-integrations' }"
      />
      <q-avatar color="primary" text-color="white" icon="policy" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="text-h6 text-weight-bold">
          {{ integration ? `Zugriff für ${integration.name}` : 'Zugriff verwalten' }}
        </div>
        <div class="text-body2 app-muted">
          Standardwerte und Abweichungen für einzelne Ressourcen festlegen.
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
        :disable="loading || loadFailed || !access || !dirty"
        @click="save"
      />
    </q-toolbar>

    <q-banner v-if="loadFailed" rounded class="app-surface-tonal q-mb-md">
      Die Ressourcen konnten nicht geladen werden.
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

    <q-form v-else-if="access" class="q-gutter-md" @submit.prevent="save">
      <q-card v-if="supportsDefaultCalendar" flat class="app-surface-tonal">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Standardkalender dieses Kontos</div>
          <div class="text-caption app-muted">
            Gilt installationsweit für unspezifische Kalenderabfragen und neue Termine.
            Eine Auswahl ersetzt den bisherigen Standard.
          </div>
        </q-card-section>
        <q-separator />
        <q-card-section>
          <q-select
            v-model="defaultResourceKey"
            outlined
            dense
            emit-value
            map-options
            label="Standardkalender"
            :options="defaultCalendarOptions"
          />
        </q-card-section>
      </q-card>

      <q-card flat class="app-surface-tonal">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Standard für Integration</div>
          <div class="text-caption app-muted">Gilt für Ressourcen ohne eigene Einstellung.</div>
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
                :label="action.label"
                :options="policyOptions(action, action.globalPolicy)"
                :hint="`Wirksam: ${policyLabel(action.effectiveIntegrationPolicy)}`"
                :disable="action.globalPolicy === 'disabled'"
                persistent-hint
              />
            </div>
          </div>
        </q-card-section>
      </q-card>

      <q-banner v-if="!access.resources.length" rounded class="app-surface-tonal">
        Diese Integration stellt derzeit keine konfigurierbaren Ressourcen bereit.
      </q-banner>

      <q-card v-for="resource in access.resources" :key="resource.key" flat class="app-surface-tonal">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">{{ resource.label }}</div>
        </q-card-section>
        <q-separator />
        <q-card-section>
          <div class="row q-col-gutter-md">
            <div v-for="action in access.actions" :key="action.capability" class="col-12 col-md-4">
              <q-select
                v-model="resourceForm[resource.key][action.capability]"
                outlined
                dense
                emit-value
                map-options
                :label="action.label"
                :options="policyOptions(action, action.effectiveIntegrationPolicy)"
                :hint="effectiveHint(resource, action)"
                :disable="action.globalPolicy === 'disabled' || resource.hardUnavailableCapabilities.includes(action.capability)"
                persistent-hint
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
          :to="{ name: 'settings-integrations' }"
        />
        <q-btn
          unelevated
          no-caps
          type="submit"
          color="primary"
          icon="save"
          label="Speichern"
          :loading="saving"
          :disable="!dirty"
        />
      </div>
    </q-form>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { useIntegrationStore } from 'stores/integrations'

defineOptions({ name: 'IntegrationAccessPage' })

const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const integrations = useIntegrationStore()
const access = ref(null)
const defaultResourceKey = ref(null)
const integrationForm = reactive({})
const resourceForm = reactive({})
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const savedPayload = ref('')
let loadToken = 0

const integrationId = computed(() => String(route.params.integrationId || ''))
const integration = computed(() => [
  ...integrations.search.providers,
  ...integrations.calendar.accounts
].find((item) => item.id === integrationId.value))
const dirty = computed(() => Boolean(
  access.value && JSON.stringify(requestPayload()) !== savedPayload.value
))
const supportsDefaultCalendar = computed(() => Boolean(
  access.value && 'defaultResourceKey' in access.value
))
const defaultCalendarOptions = computed(() => [
  { label: 'Keiner in diesem Konto', value: null },
  ...(access.value?.resources || []).map((resource) => ({
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
    ...(supportsDefaultCalendar.value
      ? { defaultResourceKey: defaultResourceKey.value }
      : {})
  }
}

function assignAccess(value) {
  defaultResourceKey.value = value.defaultResourceKey || null
  for (const key of Object.keys(integrationForm)) delete integrationForm[key]
  for (const key of Object.keys(resourceForm)) delete resourceForm[key]
  for (const action of value.actions) {
    integrationForm[action.capability] = action.integrationPolicy || 'inherit'
  }
  for (const resource of value.resources) {
    resourceForm[resource.key] = {}
    for (const action of value.actions) {
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

watch(integrationId, load, { immediate: true })
onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
