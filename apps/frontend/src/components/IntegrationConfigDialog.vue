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
              v-if="!['openscad', 'mail'].includes(form.integration_type)"
              v-model="form.base_url"
              outlined
              :label="baseUrlLabel"
              :placeholder="baseUrlPlaceholder"
              lazy-rules
              :rules="[requiredRule, urlRule]"
            />
            <template v-if="form.integration_type === 'mail'">
              <div class="text-subtitle2 text-weight-medium">Posteingang (IMAP)</div>
              <div class="row q-col-gutter-sm">
                <q-input v-model="form.imap_host" outlined label="IMAP-Host" class="col-12 col-sm" lazy-rules :rules="[requiredRule]" />
                <q-input v-model.number="form.imap_port" outlined type="number" label="IMAP-Port" class="col-5 col-sm-3" lazy-rules :rules="[portRule]" />
                <q-select v-model="form.imap_security" outlined emit-value map-options label="IMAP-Sicherheit" class="col-7 col-sm-4" :options="securityOptions" />
              </div>
              <div class="text-subtitle2 text-weight-medium">Postausgang (SMTP)</div>
              <div class="row q-col-gutter-sm">
                <q-input v-model="form.smtp_host" outlined label="SMTP-Host" class="col-12 col-sm" lazy-rules :rules="[requiredRule]" />
                <q-input v-model.number="form.smtp_port" outlined type="number" label="SMTP-Port" class="col-5 col-sm-3" lazy-rules :rules="[portRule]" />
                <q-select v-model="form.smtp_security" outlined emit-value map-options label="SMTP-Sicherheit" class="col-7 col-sm-4" :options="securityOptions" />
              </div>
              <q-input v-model="form.username" outlined label="Benutzername" lazy-rules :rules="[requiredRule]" />
              <q-input v-model="form.from_address" outlined type="email" label="Fester Absender" hint="Diese Absenderadresse wird für ausgehende E-Mails verwendet." lazy-rules :rules="[requiredRule, emailRule]" />
              <q-input
                v-model="form.password"
                outlined
                type="password"
                :label="integration?.hasCredential ? 'Neues Passwort (optional)' : 'Passwort'"
                :hint="integration?.hasCredential ? 'Leer lassen, um das gespeicherte Passwort beizubehalten.' : ''"
                lazy-rules
                :rules="isEditing ? [] : [requiredRule]"
              />
            </template>
            <q-banner
              v-if="['recipes', 'coding'].includes(form.integration_type) && form.base_url.trim().toLowerCase().startsWith('http://')"
              dense
              rounded
              class="app-surface-tonal"
              role="alert"
            >
              <template #avatar><q-icon name="warning_amber" color="warning" /></template>
              HTTP überträgt Zugangsdaten unverschlüsselt. Verwende es nur in einem vertrauenswürdigen lokalen Netz.
            </q-banner>
            <div v-if="form.integration_type === 'openscad'" class="row items-start q-col-gutter-sm">
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
              <div class="col-auto q-pt-sm">
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
            <template v-if="form.integration_type === 'coding'">
              <q-input
                v-model="form.username"
                outlined
                label="Benutzername"
                lazy-rules
                :rules="[requiredRule]"
              />
              <q-input
                v-model="form.password"
                outlined
                type="password"
                :label="integration?.hasCredential ? 'Neues Passwort (optional)' : 'Passwort (optional)'"
                :hint="integration?.hasCredential ? 'Leer lassen, um das gespeicherte Passwort beizubehalten.' : ''"
                :disable="form.removePassword"
              />
              <q-toggle
                v-if="isEditing && integration?.hasCredential"
                v-model="form.removePassword"
                color="negative"
                label="Gespeichertes Passwort entfernen"
                @update:model-value="form.password = ''"
              />
            </template>
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
  { label: 'Mail', value: 'mail' },
  { label: 'Wissensdatenbank', value: 'knowledge' },
  { label: 'Rezepte & Essensplanung', value: 'recipes' },
  { label: '3D-Modellierung', value: 'openscad' },
  { label: 'Programmierung', value: 'coding' }
]
const PROVIDER_DEFAULTS = {
  search: { provider: 'searxng', label: 'SearXNG', placeholder: 'https://search.example.com' },
  calendar: { provider: 'caldav', label: 'CalDAV', placeholder: 'https://calendar.example.com' },
  mail: { provider: 'imap_smtp', label: 'IMAP/SMTP', placeholder: '' },
  knowledge: { provider: 'bookstack', label: 'BookStack', placeholder: 'https://wiki.example.com' },
  recipes: { provider: 'mealie', label: 'Mealie', placeholder: 'http://mealie.example.com' },
  openscad: { provider: 'openscad', label: 'OpenSCAD', placeholder: '' },
  coding: { provider: 'opencode', label: 'OpenCode', placeholder: 'http://localhost:4096' }
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
const requiresHttps = computed(() => !['search', 'recipes', 'coding'].includes(form.integration_type))
const dialogIcon = computed(() => ({ coding: 'code', mail: 'mail', openscad: 'view_in_ar' })[form.integration_type] || 'extension')
const securityOptions = [
  { label: 'TLS', value: 'tls' },
  { label: 'STARTTLS', value: 'starttls' }
]
const executionAgentOptions = computed(() => {
  const options = executionAgents.items.map((agent) => ({ label: agent.alias, value: agent.id }))
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
    removePassword: false,
    imap_host: '',
    imap_port: 993,
    imap_security: 'tls',
    smtp_host: '',
    smtp_port: 465,
    smtp_security: 'tls',
    from_address: '',
    token_id: '',
    token_secret: '',
    token: '',
    executionAgentId: null,
    binaryPath: '/usr/bin/openscad',
    enabled: true
  }
}

function selectIntegrationType(type) {
  const preset = PROVIDER_DEFAULTS[type] || PROVIDER_DEFAULTS.search
  form.provider_type = preset.provider
  form.name = preset.label
  if (type === 'coding' && !form.username) form.username = 'opencode'
  invalidateDiscovery()
  if (type === 'openscad') void fetchComputers()
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

function portRule(value) {
  const port = Number(value)
  return (Number.isInteger(port) && port >= 1 && port <= 65535) || 'Bitte gib einen gültigen Port ein.'
}

function emailRule(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || '')) || 'Bitte gib eine gültige E-Mail-Adresse ein.'
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
    if (form.integration_type === 'mail') {
      const payload = {
        integration_type: 'mail',
        provider_type: 'imap_smtp',
        name: form.name.trim(),
        imap_host: form.imap_host.trim(),
        imap_port: Number(form.imap_port),
        imap_security: form.imap_security,
        smtp_host: form.smtp_host.trim(),
        smtp_port: Number(form.smtp_port),
        smtp_security: form.smtp_security,
        username: form.username.trim(),
        from_address: form.from_address.trim(),
        password: form.password,
        enabled: form.enabled
      }
      if (props.integration) await integrations.updateMail(props.integration.id, payload)
      else await integrations.createMail(payload)
      $q.notify({ type: 'positive', message: 'Integration gespeichert' })
      close()
      return
    }
    if (form.integration_type === 'coding') {
      const payload = {
        name: form.name.trim(),
        baseUrl: form.base_url.trim(),
        username: form.username.trim(),
        password: form.password,
        removePassword: form.removePassword,
        enabled: form.enabled
      }
      if (props.integration) await integrations.updateOpenCode(props.integration.id, payload)
      else await integrations.createOpenCode(payload)
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
    imap_host: props.integration.config?.imapHost || '',
    imap_port: props.integration.config?.imapPort || 993,
    imap_security: props.integration.config?.imapSecurity || 'tls',
    smtp_host: props.integration.config?.smtpHost || '',
    smtp_port: props.integration.config?.smtpPort || 465,
    smtp_security: props.integration.config?.smtpSecurity || 'tls',
    from_address: props.integration.config?.fromAddress || '',
    password: '',
    removePassword: false,
    token_id: '',
    token_secret: '',
    token: '',
    executionAgentId: props.integration.executionAgentId
      || props.integration.config?.executionAgentId
      || null,
    binaryPath: props.integration.config?.binaryPath || '/usr/bin/openscad',
    enabled: props.integration.enabled
  } : {})
  if (!props.integration && integrationTypes.value.length) {
    form.integration_type = integrationTypes.value[0].value
    selectIntegrationType(form.integration_type)
  }
  if (form.integration_type === 'openscad') void fetchComputers()
})
</script>
