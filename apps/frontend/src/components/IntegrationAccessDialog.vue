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
            Lege für jede Ressource fest, ob der Agent nachfragen muss.
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

        <q-banner v-else-if="access && !access.resources.length" rounded class="app-surface-tonal">
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
              v-model="form[resource.key][action.capability]"
              outlined
              dense
              emit-value
              map-options
              :label="action.label"
              :options="policyOptions(action)"
              :hint="effectiveHint(resource, action)"
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
const form = reactive({})
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
let loadToken = 0

function policyLabel(policy) {
  return {
    disabled: 'Deaktiviert',
    ask: 'Nachfragen',
    auto: 'Automatisch'
  }[policy] || policy
}

function policyOptions(action) {
  return [
    { label: `Erben: ${policyLabel(action.globalPolicy)}`, value: 'inherit' },
    { label: 'Deaktiviert', value: 'disabled' },
    { label: 'Nachfragen', value: 'ask' },
    { label: 'Automatisch', value: 'auto' }
  ]
}

function effectiveHint(resource, action) {
  const selected = form[resource.key]?.[action.capability] || 'inherit'
  const effective = action.globalPolicy === 'disabled'
    ? 'disabled'
    : selected === 'inherit'
      ? action.globalPolicy
      : selected
  return `Wirksam: ${policyLabel(effective)}`
}

function assignAccess(value) {
  access.value = value
  for (const key of Object.keys(form)) delete form[key]
  for (const resource of value.resources) {
    form[resource.key] = {}
    for (const action of value.actions) {
      form[resource.key][action.capability] = resource.policies[action.capability] || 'inherit'
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
  const resources = {}
  for (const resource of access.value.resources) {
    const policies = Object.fromEntries(
      Object.entries(form[resource.key]).filter(([, policy]) => policy !== 'inherit')
    )
    if (Object.keys(policies).length) resources[resource.key] = policies
  }
  saving.value = true
  try {
    const value = await integrations.saveAccess(integrationId, resources)
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
