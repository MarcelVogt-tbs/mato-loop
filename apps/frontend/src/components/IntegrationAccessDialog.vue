<template>
  <q-dialog
    :model-value="modelValue"
    :maximized="$q.screen.lt.sm"
    :persistent="saving"
    @hide="close"
  >
    <q-card class="integration-access-dialog app-surface column">
      <q-card-section class="row items-start no-wrap q-gutter-md">
        <q-avatar color="primary" text-color="white" icon="policy" />
        <div class="col">
          <div class="text-h6">Zugriff für {{ integration?.name }}</div>
          <div class="text-body2 app-muted">
            Lege Standardwerte und Abweichungen für einzelne Ressourcen fest.
          </div>
        </div>
        <q-btn flat round dense icon="close" aria-label="Dialog schließen" @click="close" />
      </q-card-section>

      <q-separator />
      <q-card-section class="col scroll q-gutter-md">
        <q-banner v-if="loadFailed" rounded class="app-surface-tonal">
          Die Ressourcen konnten nicht geladen werden.
          <template #action>
            <q-btn flat no-caps color="primary" label="Erneut laden" @click="load" />
          </template>
        </q-banner>

        <q-card v-if="access" flat class="app-surface-tonal integration-access-resource">
          <q-card-section>
            <div class="text-subtitle1 text-weight-bold">Standard für Integration</div>
            <div class="text-caption app-muted">Gilt für Ressourcen ohne eigene Einstellung.</div>
          </q-card-section>
          <q-separator />
          <q-card-section class="integration-access-actions">
            <q-select
              v-for="action in access.actions"
              :key="action.capability"
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
          </q-card-section>
        </q-card>

        <q-banner v-if="access && !access.resources.length" rounded class="app-surface-tonal">
          Diese Integration stellt derzeit keine konfigurierbaren Ressourcen bereit.
        </q-banner>

        <q-card
          v-for="resource in access?.resources || []"
          :key="resource.key"
          flat
          class="app-surface-tonal integration-access-resource"
        >
          <q-card-section>
            <div class="text-subtitle1 text-weight-bold">{{ resource.label }}</div>
          </q-card-section>
          <q-separator />
          <q-card-section class="integration-access-actions">
            <q-select
              v-for="action in access.actions"
              :key="action.capability"
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
          </q-card-section>
        </q-card>

        <q-inner-loading :showing="loading" />
      </q-card-section>

      <q-separator />
      <q-card-actions class="q-pa-md">
        <q-btn flat no-caps label="Abbrechen" @click="close" />
        <q-space />
        <q-btn
          unelevated
          no-caps
          color="primary"
          label="Zugriff speichern"
          :loading="saving"
          :disable="loading || loadFailed || !access"
          @click="save"
        />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useIntegrationStore } from 'stores/integrations'

defineOptions({ name: 'IntegrationAccessDialog' })

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  integration: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue'])

const $q = useQuasar()
const integrations = useIntegrationStore()
const access = ref(null)
const integrationForm = reactive({})
const resourceForm = reactive({})
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
let loadToken = 0

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

function assignAccess(value) {
  access.value = value
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
}

async function load() {
  if (!props.integration) return
  const token = ++loadToken
  const integrationId = props.integration.id
  loading.value = true
  loadFailed.value = false
  try {
    const value = await integrations.fetchAccess(integrationId)
    if (
      token === loadToken
      && props.modelValue
      && props.integration?.id === integrationId
    ) {
      assignAccess(value)
    }
  } catch {
    if (token === loadToken) loadFailed.value = true
  } finally {
    if (token === loadToken) loading.value = false
  }
}

async function save() {
  if (!props.integration || !access.value) return
  const integrationId = props.integration.id
  const policies = Object.fromEntries(
    Object.entries(integrationForm).filter(([, policy]) => policy !== 'inherit')
  )
  const resources = {}
  for (const resource of access.value.resources) {
    const resourcePolicies = Object.fromEntries(
      Object.entries(resourceForm[resource.key]).filter(([, policy]) => policy !== 'inherit')
    )
    if (Object.keys(resourcePolicies).length) resources[resource.key] = resourcePolicies
  }
  saving.value = true
  try {
    const value = await integrations.saveAccess(integrationId, policies, resources)
    if (props.integration?.id !== integrationId) return
    assignAccess(value)
    $q.notify({ type: 'positive', message: 'Zugriffsrichtlinien gespeichert' })
    close()
  } finally {
    saving.value = false
  }
}

function close() {
  loadToken += 1
  emit('update:modelValue', false)
}

watch(() => props.modelValue, (open) => {
  if (open) load()
  else {
    loadToken += 1
    access.value = null
    loading.value = false
  }
})
</script>
