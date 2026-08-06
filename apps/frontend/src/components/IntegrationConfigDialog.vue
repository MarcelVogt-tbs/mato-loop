<template>
  <q-dialog
    :model-value="modelValue"
    :maximized="$q.screen.lt.sm"
    :persistent="saving"
    @hide="close"
  >
    <q-card class="integration-dialog app-surface column">
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
      <q-form ref="formRef" class="col column no-wrap">
        <q-card-section class="col scroll q-gutter-md">
          <q-select
            v-model="form.integration_type"
            outlined
            emit-value
            map-options
            label="Integrationstyp"
            :options="integrationTypes"
            :disable="isEditing"
            @update:model-value="selectIntegrationType"
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

          <template v-if="providerTypes.some(({ value }) => value === form.provider_type)">
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
              :label="form.integration_type === 'calendar' ? 'CalDAV Base URL' : 'SearXNG Base URL'"
              :placeholder="form.integration_type === 'calendar' ? 'https://calendar.example.com' : 'https://search.example.com'"
              lazy-rules
              :rules="[requiredRule, urlRule]"
            />
            <q-input
              v-if="form.integration_type === 'calendar'"
              v-model="form.username"
              outlined
              label="Benutzername"
              lazy-rules
              :rules="[requiredRule]"
            />
            <q-input
              v-if="form.integration_type === 'calendar'"
              v-model="form.password"
              outlined
              type="password"
              :label="integration?.hasCredential ? 'Neues Passwort (optional)' : 'Passwort'"
              :hint="integration?.hasCredential ? 'Leer lassen, um das gespeicherte Passwort beizubehalten.' : ''"
              lazy-rules
              :rules="isEditing ? [] : [requiredRule]"
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
const integrationTypes = [
  { label: 'Websuche', value: 'search' },
  { label: 'Kalender', value: 'calendar' }
]
const providerTypes = computed(() => form.integration_type === 'calendar'
  ? [{ label: 'CalDAV', value: 'caldav' }]
  : [{ label: 'SearXNG', value: 'searxng' }])
const form = reactive(defaultForm())
const isEditing = computed(() => Boolean(props.integration))

function defaultForm() {
  return {
    integration_type: 'search',
    provider_type: 'searxng',
    name: 'SearXNG',
    base_url: '',
    username: '',
    password: '',
    enabled: true
  }
}

function selectIntegrationType(type) {
  form.provider_type = type === 'calendar' ? 'caldav' : 'searxng'
  form.name = type === 'calendar' ? 'CalDAV' : 'SearXNG'
}

function requiredRule(value) {
  return Boolean(String(value || '').trim()) || 'Pflichtfeld'
}

function urlRule(value) {
  try {
    const protocol = new URL(value).protocol
    const allowed = form.integration_type === 'calendar'
      ? protocol === 'https:'
      : ['http:', 'https:'].includes(protocol)
    return allowed || (form.integration_type === 'calendar'
      ? 'CalDAV erfordert eine HTTPS-URL.'
      : 'Bitte gib eine gültige HTTP-URL ein.')
  } catch {
    return form.integration_type === 'calendar'
      ? 'CalDAV erfordert eine gültige HTTPS-URL.'
      : 'Bitte gib eine gültige HTTP-URL ein.'
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
    if (form.integration_type === 'calendar') {
      payload.username = form.username.trim()
      payload.password = form.password
      if (props.integration) await integrations.updateCalendar(props.integration.id, payload)
      else await integrations.createCalendar(payload)
    } else if (props.integration) await integrations.updateSearch(props.integration.id, payload)
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
    username: props.integration.config?.username || '',
    password: '',
    enabled: props.integration.enabled
  } : {})
})
</script>
