<template>
  <q-dialog
    :model-value="modelValue"
    :maximized="$q.screen.lt.sm"
    :persistent="saving"
    @hide="close"
  >
    <q-card class="integration-dialog app-surface column">
      <q-card-section class="row items-start no-wrap q-gutter-md">
        <q-avatar color="primary" text-color="white" :icon="dialogIcon" />
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
              v-if="!['git', 'openscad'].includes(form.integration_type)"
              v-model="form.base_url"
              outlined
              :label="baseUrlLabel"
              :placeholder="baseUrlPlaceholder"
              lazy-rules
              :rules="[requiredRule, urlRule]"
            />
            <q-banner
              v-if="form.integration_type === 'recipes' && form.base_url.trim().toLowerCase().startsWith('http://')"
              dense
              rounded
              class="app-surface-tonal"
              role="alert"
            >
              <template #avatar><q-icon name="warning_amber" color="warning" /></template>
              HTTP überträgt den Mealie-Token unverschlüsselt. Verwende es nur in einem vertrauenswürdigen lokalen Netz.
            </q-banner>
            <div v-if="['git', 'openscad'].includes(form.integration_type)" class="row items-start q-col-gutter-sm">
              <q-select
                v-model="form.executionAgentId"
                class="col"
                outlined
                emit-value
                map-options
                label="Computer"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                :options="executionAgentOptions"
                :loading="executionAgents.loading"
                lazy-rules
                :rules="[requiredRule]"
                @update:model-value="invalidateDiscovery"
              >
                <template #no-option>
                  <q-item>
                    <q-item-section class="app-muted">Kein Computer eingerichtet</q-item-section>
                  </q-item>
                </template>
              </q-select>
              <div v-if="form.integration_type === 'openscad'" class="col-auto q-pt-sm">
                <q-btn
                  outline
                  dense
                  no-caps
                  color="primary"
                  icon="search"
                  label="Erkennen"
                  :disable="!form.executionAgentId"
                  :loading="discovering"
                  @click="discoverOpenScad"
                />
              </div>
            </div>
            <q-toggle
              v-if="form.integration_type === 'git'"
              v-model="form.allowNetwork"
              color="primary"
              label="Netzwerkzugriff erlauben"
            />
            <q-input
              v-if="form.integration_type === 'openscad'"
              v-model="form.binaryPath"
              outlined
              label="Binary-Pfad"
              hint="Absoluter Pfad zur OpenSCAD-Binärdatei."
              lazy-rules
              :rules="[requiredRule, absolutePathRule]"
            />
            <q-banner
              v-if="form.integration_type === 'openscad' && discovery"
              dense
              rounded
              class="app-surface-tonal"
              role="status"
              aria-live="polite"
            >
              <div class="text-weight-medium">{{ discoveryStatus }}</div>
              <div v-if="discovery.version" class="text-caption q-mt-xs">Version: {{ discovery.version }}</div>
              <div class="text-caption">
                Headless-Vorschau {{ discovery.headlessPreview ? 'verfügbar' : 'nicht verfügbar' }}
              </div>
            </q-banner>
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
            <q-input
              v-if="form.integration_type === 'knowledge'"
              v-model="form.token_id"
              outlined
              :label="integration?.hasCredential ? 'Neue Token-ID (optional)' : 'Token-ID'"
              :hint="integration?.hasCredential ? 'Leer lassen, um den gespeicherten API-Token beizubehalten.' : 'API-Token aus dem BookStack-Benutzerprofil.'"
              lazy-rules
              :rules="isEditing ? [tokenPairRule] : [requiredRule]"
            />
            <q-input
              v-if="form.integration_type === 'recipes'"
              v-model="form.token"
              outlined
              type="password"
              :label="integration?.hasCredential ? 'Neuer API-Token (optional)' : 'API-Token'"
              :hint="integration?.hasCredential ? 'Leer lassen, um den gespeicherten API-Token beizubehalten.' : 'Langlebiger API-Token aus dem Mealie-Benutzerprofil.'"
              lazy-rules
              :rules="isEditing ? [] : [requiredRule]"
            />
            <q-input
              v-if="form.integration_type === 'knowledge'"
              v-model="form.token_secret"
              outlined
              type="password"
              :label="integration?.hasCredential ? 'Neues Token-Secret (optional)' : 'Token-Secret'"
              lazy-rules
              :rules="isEditing ? [tokenPairRule] : [requiredRule]"
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
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'

defineOptions({ name: 'IntegrationConfigDialog' })

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  integration: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue'])

const $q = useQuasar()
const integrations = useIntegrationStore()
const executionAgents = useExecutionAgentStore()
const formRef = ref(null)
const saving = ref(false)
const allIntegrationTypes = [
  { label: 'Websuche', value: 'search' },
  { label: 'Kalender & Aufgaben', value: 'calendar' },
  { label: 'Wissensdatenbank', value: 'knowledge' },
  { label: 'Rezepte & Essensplanung', value: 'recipes' },
  { label: 'Versionsverwaltung', value: 'git' },
  { label: '3D-Modellierung', value: 'openscad' }
]
const PROVIDER_DEFAULTS = {
  search: { provider: 'searxng', label: 'SearXNG', placeholder: 'https://search.example.com' },
  calendar: { provider: 'caldav', label: 'CalDAV', placeholder: 'https://calendar.example.com' },
  knowledge: { provider: 'bookstack', label: 'BookStack', placeholder: 'https://wiki.example.com' },
  recipes: { provider: 'mealie', label: 'Mealie', placeholder: 'http://mealie.example.com' },
  git: { provider: 'git', label: 'Git', placeholder: '' },
  openscad: { provider: 'openscad', label: 'OpenSCAD', placeholder: '' }
}
const integrationTypes = computed(() => allIntegrationTypes.filter((type) => {
  const adapterType = PROVIDER_DEFAULTS[type.value].provider
  return integrations.adapterAvailable(adapterType)
}))
const providerTypes = computed(() => {
  const preset = PROVIDER_DEFAULTS[form.integration_type] || PROVIDER_DEFAULTS.search
  return [{ label: preset.label, value: preset.provider }]
})
const baseUrlLabel = computed(
  () => `${(PROVIDER_DEFAULTS[form.integration_type] || PROVIDER_DEFAULTS.search).label}-Basis-URL`
)
const baseUrlPlaceholder = computed(
  () => (PROVIDER_DEFAULTS[form.integration_type] || PROVIDER_DEFAULTS.search).placeholder
)
const requiresHttps = computed(() => !['search', 'recipes'].includes(form.integration_type))
const dialogIcon = computed(() => form.integration_type === 'openscad' ? 'view_in_ar' : 'extension')
const executionAgentOptions = computed(() => {
  const options = executionAgents.items.map((agent) => ({ label: agent.name, value: agent.id }))
  if (form.executionAgentId && !executionAgents.byId(form.executionAgentId)) {
    options.unshift({ label: 'Nicht verfügbarer Computer', value: form.executionAgentId })
  }
  return options
})
const form = reactive(defaultForm())
const isEditing = computed(() => Boolean(props.integration))
const discovering = ref(false)
const discovery = ref(null)
let discoveryVersion = 0
const discoveryStatus = computed(() => {
  const availability = discovery.value?.availability ?? discovery.value?.available
  const available = availability === true || availability === 'available'
  return available ? 'OpenSCAD wurde gefunden.' : 'OpenSCAD wurde nicht gefunden.'
})

function defaultForm() {
  return {
    integration_type: 'search',
    provider_type: 'searxng',
    name: 'SearXNG',
    base_url: '',
    username: '',
    password: '',
    token_id: '',
    token_secret: '',
    token: '',
    executionAgentId: null,
    allowNetwork: false,
    binaryPath: '/usr/bin/openscad',
    enabled: true
  }
}

function selectIntegrationType(type) {
  const preset = PROVIDER_DEFAULTS[type] || PROVIDER_DEFAULTS.search
  form.provider_type = preset.provider
  form.name = preset.label
  invalidateDiscovery()
  if (['git', 'openscad'].includes(type)) void fetchComputers()
}

async function fetchComputers() {
  try {
    await executionAgents.fetchAll()
  } catch {
    // The central API interceptor displays the error as a toast.
  }
}

function requiredRule(value) {
  return Boolean(String(value || '').trim()) || 'Pflichtfeld'
}

function absolutePathRule(value) {
  return String(value || '').trim().startsWith('/') || 'Bitte gib einen absoluten Pfad ein.'
}

async function discoverOpenScad() {
  if (!form.executionAgentId || discovering.value) return
  const version = ++discoveryVersion
  const executionAgentId = form.executionAgentId
  discovering.value = true
  try {
    const result = await integrations.discoverOpenScad(executionAgentId)
    if (
      version !== discoveryVersion
      || !props.modelValue
      || form.integration_type !== 'openscad'
      || form.executionAgentId !== executionAgentId
    ) return
    discovery.value = result
    const path = result?.executablePath
    if (path) form.binaryPath = path
  } catch {
    if (version === discoveryVersion) discovery.value = null
  } finally {
    if (version === discoveryVersion) discovering.value = false
  }
}

function invalidateDiscovery() {
  discoveryVersion += 1
  discovering.value = false
  discovery.value = null
}

function tokenPairRule() {
  const hasId = Boolean(String(form.token_id || '').trim())
  const hasSecret = Boolean(form.token_secret)
  if (hasId === hasSecret) return true
  return 'Bitte Token-ID und Token-Secret gemeinsam angeben oder beide leer lassen.'
}

function urlRule(value) {
  const label = (PROVIDER_DEFAULTS[form.integration_type] || PROVIDER_DEFAULTS.search).label
  try {
    const protocol = new URL(value).protocol
    const allowed = requiresHttps.value
      ? protocol === 'https:'
      : ['http:', 'https:'].includes(protocol)
    return allowed || (requiresHttps.value
      ? `${label} erfordert eine HTTPS-URL.`
      : 'Bitte gib eine gültige HTTP-URL ein.')
  } catch {
    return requiresHttps.value
      ? `${label} erfordert eine gültige HTTPS-URL.`
      : 'Bitte gib eine gültige HTTP-URL ein.'
  }
}

async function save() {
  if (!await formRef.value?.validate()) return
  if (!integrations.adapterAvailable(form.provider_type)) {
    $q.notify({ type: 'warning', message: 'Dieser Integrationstyp ist nicht verfügbar.' })
    return
  }
  saving.value = true
  try {
    if (form.integration_type === 'git') {
      const payload = {
        executionAgentId: form.executionAgentId,
        config: { allowNetwork: form.allowNetwork },
        name: form.name.trim(),
        enabled: form.enabled
      }
      if (props.integration) await integrations.updateGit(props.integration.id, payload)
      else await integrations.createGit(payload)
      $q.notify({ type: 'positive', message: 'Integration gespeichert' })
      close()
      return
    }
    if (form.integration_type === 'openscad') {
      const payload = {
        executionAgentId: form.executionAgentId,
        name: form.name.trim(),
        config: { binaryPath: form.binaryPath.trim() },
        enabled: form.enabled
      }
      if (props.integration) await integrations.updateOpenScad(props.integration.id, payload)
      else await integrations.createOpenScad(payload)
      $q.notify({ type: 'positive', message: 'Integration gespeichert' })
      close()
      return
    }
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
    } else if (form.integration_type === 'knowledge') {
      payload.token_id = form.token_id.trim()
      payload.token_secret = form.token_secret
      if (props.integration) await integrations.updateKnowledge(props.integration.id, payload)
      else await integrations.createKnowledge(payload)
    } else if (form.integration_type === 'recipes') {
      payload.token = form.token
      if (props.integration) await integrations.updateRecipes(props.integration.id, payload)
      else await integrations.createRecipes(payload)
    } else if (props.integration) await integrations.updateSearch(props.integration.id, payload)
    else await integrations.createSearch(payload)
    $q.notify({ type: 'positive', message: 'Integration gespeichert' })
    close()
  } finally {
    saving.value = false
  }
}

function close() {
  invalidateDiscovery()
  emit('update:modelValue', false)
}

watch([() => props.modelValue, () => props.integration?.id], ([open]) => {
  invalidateDiscovery()
  if (!open) return
  Object.assign(form, defaultForm(), props.integration ? {
    integration_type: props.integration.integrationType,
    provider_type: props.integration.providerType,
    name: props.integration.name,
    base_url: props.integration.config?.baseUrl || '',
    username: props.integration.config?.username || '',
    password: '',
    token_id: '',
    token_secret: '',
    token: '',
    executionAgentId: props.integration.executionAgentId
      || props.integration.config?.executionAgentId
      || null,
    allowNetwork: props.integration.allowNetwork
      ?? props.integration.config?.allowNetwork
      ?? false,
    binaryPath: props.integration.config?.binaryPath || '/usr/bin/openscad',
    enabled: props.integration.enabled
  } : {})
  if (!props.integration && integrationTypes.value.length) {
    form.integration_type = integrationTypes.value[0].value
    selectIntegrationType(form.integration_type)
  }
  if (['git', 'openscad'].includes(form.integration_type)) void fetchComputers()
})
</script>
