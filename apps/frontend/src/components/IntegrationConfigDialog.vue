<template>
  <q-dialog :model-value="modelValue" @hide="close">
    <q-card class="integration-dialog app-surface">
      <q-card-section class="row items-start no-wrap q-gutter-md">
        <q-avatar color="primary" text-color="white" icon="extension" />
        <div class="col">
          <div class="text-h6">{{ isEditing ? `${integration.name} bearbeiten` : 'Integration hinzufügen' }}</div>
          <div class="text-body2 app-muted">
            Wähle die Fähigkeit und den dazugehörigen Anbieter.
          </div>
        </div>
        <q-btn flat round dense icon="close" aria-label="Dialog schließen" @click="close" />
      </q-card-section>

      <q-separator />
      <q-form ref="formRef">
        <q-card-section class="q-gutter-md">
          <q-select
            v-model="form.integration_type"
            outlined
            emit-value
            map-options
            label="Integrationstyp"
            :options="integrationTypes"
            :disable="isEditing"
          />
          <q-select
            v-model="form.provider_type"
            outlined
            emit-value
            map-options
            label="Anbieter"
            :options="providerTypes"
            :disable="isEditing"
          />

          <template v-if="form.integration_type === 'search' && form.provider_type === 'searxng'">
            <q-input
              v-model="form.name"
              outlined
              label="Name"
              lazy-rules
              :rules="[requiredRule]"
            />
            <q-input
              v-model="form.base_url"
              outlined
              label="SearXNG Base URL"
              placeholder="https://search.example.com"
              lazy-rules
              :rules="[requiredRule, urlRule]"
            />
            <q-toggle v-model="form.enabled" color="primary" label="Integration aktivieren" />
          </template>
        </q-card-section>
      </q-form>

      <q-separator />
      <q-card-actions class="q-pa-md">
        <q-btn flat no-caps label="Abbrechen" @click="close" />
        <q-space />
        <q-btn
          unelevated
          no-caps
          color="primary"
          :label="isEditing ? 'Änderungen speichern' : 'Integration hinzufügen'"
          :loading="saving"
          @click="save"
        />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useIntegrationStore } from 'stores/integrations'

defineOptions({ name: 'IntegrationConfigDialog' })

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  integration: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue'])

const $q = useQuasar()
const integrations = useIntegrationStore()
const formRef = ref(null)
const saving = ref(false)
const integrationTypes = [{ label: 'Websuche', value: 'search' }]
const providerTypes = computed(() => form.integration_type === 'search'
  ? [{ label: 'SearXNG', value: 'searxng' }]
  : [])
const form = reactive(defaultForm())
const isEditing = computed(() => Boolean(props.integration))

function defaultForm() {
  return {
    integration_type: 'search',
    provider_type: 'searxng',
    name: 'SearXNG',
    base_url: '',
    enabled: true
  }
}

function requiredRule(value) {
  return Boolean(String(value || '').trim()) || 'Pflichtfeld'
}

function urlRule(value) {
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol) || 'Bitte gib eine gültige HTTP-URL ein.'
  } catch {
    return 'Bitte gib eine gültige HTTP-URL ein.'
  }
}

async function save() {
  if (!await formRef.value?.validate()) return
  saving.value = true
  try {
    const payload = {
      integration_type: form.integration_type,
      provider_type: form.provider_type,
      name: form.name.trim(),
      base_url: form.base_url.trim(),
      enabled: form.enabled
    }
    if (props.integration) await integrations.updateSearch(props.integration.id, payload)
    else await integrations.createSearch(payload)
    $q.notify({ type: 'positive', message: 'Integration gespeichert' })
    close()
  } finally {
    saving.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}

watch(() => props.modelValue, (open) => {
  if (!open) return
  Object.assign(form, defaultForm(), props.integration ? {
    integration_type: props.integration.integrationType,
    provider_type: props.integration.providerType,
    name: props.integration.name,
    base_url: props.integration.config?.baseUrl || '',
    enabled: props.integration.enabled
  } : {})
})
</script>
