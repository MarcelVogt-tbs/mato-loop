<template>
  <section :aria-busy="servers.accessLoading">
    <q-toolbar class="settings-detail-toolbar q-px-none q-mb-md">
      <q-btn flat round dense icon="arrow_back" aria-label="Zurück zu den MCP-Servern" :disable="servers.savingAccess" :to="{ name: 'settings-mcp', query: { scope } }" />
      <q-avatar color="primary" text-color="white" icon="policy" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="text-h6 text-weight-bold">{{ server ? `Zugriff für ${server.name}` : 'MCP-Zugriff' }}</div>
        <div class="text-body2 app-muted">Globale, serverweite und toolspezifische Richtlinien prüfen und festlegen.</div>
      </q-toolbar-title>
      <q-btn unelevated no-caps no-wrap color="primary" icon="save" label="Speichern" :loading="servers.savingAccess" :disable="servers.accessLoading || servers.accessLoadFailed || !dirty" @click="save" />
    </q-toolbar>

    <div class="row justify-end q-gutter-sm q-mb-md">
      <q-btn-toggle :model-value="servers.trust" no-caps unelevated toggle-color="primary" :options="trustOptions" :disable="servers.accessLoading || servers.savingAccess" @update:model-value="switchTrust" />
    </div>
    <q-banner rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="info_outline" color="primary" /></template>
      Tool-Namen und Beschreibungen stammen vom MCP-Server und sind nicht verifiziert. Sie ändern keine Richtlinie automatisch.
    </q-banner>
    <q-banner v-if="servers.accessLoadFailed" rounded class="app-surface-tonal q-mb-md">
      Die MCP-Zugriffsrichtlinien konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="primary" label="Erneut laden" @click="load" /></template>
    </q-banner>
    <q-banner v-if="personalBlocked" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="block" color="warning" /></template>
      Persönliche MCP-Server sind für diese Instanz deaktiviert.
    </q-banner>

    <q-form v-if="access" class="q-gutter-md" @submit.prevent="save">
      <q-card flat class="app-surface-tonal">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Globaler Standard für mcp.call</div>
          <div class="text-caption app-muted">Gilt für MCP-Server ohne eigene Einstellung.</div>
        </q-card-section>
        <q-separator />
        <q-card-section>
          <q-select v-if="scope === 'instance'" v-model="globalPolicy" outlined dense emit-value map-options label="Ausführungsrichtlinie" :options="policyOptions" :hint="`Wirksam: ${policyLabel(effectiveGlobalPolicy)}`" persistent-hint />
          <div v-else>
            <q-chip outline color="primary">{{ policyLabel(effectiveGlobalPolicy) }}</q-chip>
            <div class="text-caption app-muted">Wird von der Instanz vorgegeben und kann hier nicht geändert werden.</div>
          </div>
        </q-card-section>
      </q-card>

      <q-card flat class="app-surface-tonal">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Standard für diesen Server</div>
          <div class="text-caption app-muted">Gilt für erkannte Tools ohne eigene Einstellung.</div>
        </q-card-section>
        <q-separator />
        <q-card-section>
          <q-select v-model="serverPolicy" outlined dense emit-value map-options label="Serverrichtlinie" :options="inheritedOptions(effectiveGlobalPolicy)" :hint="`Wirksam: ${policyLabel(effectiveServerPolicy)}`" persistent-hint />
        </q-card-section>
      </q-card>

      <q-banner v-if="!access.resources.length" rounded class="app-surface-tonal">Dieser Server stellt derzeit keine Tools bereit.</q-banner>
      <q-card v-for="resource in access.resources" :key="resource.key" flat class="app-surface-tonal">
        <q-card-section class="row items-start q-col-gutter-md">
          <div class="col-12 col-md">
            <div class="row items-center q-gutter-sm">
              <div class="text-subtitle2 text-weight-bold">{{ toolTitle(resource) }}</div>
              <q-chip v-for="hint in annotationHints(resource)" :key="hint" dense outline color="warning">{{ hint }}</q-chip>
            </div>
            <div v-if="toolDescription(resource)" class="text-caption app-muted">{{ toolDescription(resource) }}</div>
            <div v-if="toolTitle(resource) !== resource.label" class="text-caption app-muted">Technischer Name: {{ resource.label }}</div>
            <div class="text-caption text-warning">Serverangabe, nicht verifiziert</div>
          </div>
          <div class="col-12 col-md-5">
            <q-select v-model="toolPolicies[resource.key]" outlined dense emit-value map-options label="Tool-Richtlinie" :options="inheritedOptions(effectiveServerPolicy)" :hint="`Wirksam: ${policyLabel(effectiveToolPolicy(resource))}`" persistent-hint />
          </div>
        </q-card-section>
      </q-card>
      <div class="row justify-end q-gutter-sm q-pb-md">
        <q-btn flat no-caps label="Zurück" :to="{ name: 'settings-mcp', query: { scope } }" />
        <q-btn unelevated no-caps type="submit" color="primary" icon="save" label="Speichern" :loading="servers.savingAccess" :disable="!dirty" />
      </div>
    </q-form>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import { useAuthStore } from 'stores/auth'
import { useMcpServerStore } from 'stores/mcp-servers'

defineOptions({ name: 'McpAccessPage' })
const $q = useQuasar()
const route = useRoute()
const { confirm } = useConfirm()
const auth = useAuthStore()
const servers = useMcpServerStore()
const access = ref(null)
const globalPolicy = ref('ask')
const serverPolicy = ref('inherit')
const toolPolicies = reactive({})
const savedPayload = ref('')
const trustOptions = [
  { label: 'Private Modelle', value: 'private', icon: 'lock' },
  { label: 'Öffentliche Modelle', value: 'public', icon: 'public' }
]
const policyOptions = [
  { label: 'Deaktiviert', value: 'disabled' },
  { label: 'Nachfragen', value: 'ask' },
  { label: 'Automatisch', value: 'auto' }
]
const serverId = computed(() => String(route.params.serverId || ''))
const scope = computed(() => resolveSettingsScope(route.query.scope, auth.isAdmin))
const server = computed(() => servers.servers.find((item) => item.id === serverId.value))
const personalBlocked = computed(() => scope.value === 'personal' && servers.settingsLoaded && !servers.personalAllowed)
const dirty = computed(() => Boolean(access.value && JSON.stringify(payload()) !== savedPayload.value))
const effectiveGlobalPolicy = computed(() => globalPolicy.value === 'inherit' ? access.value?.effectiveGlobalPolicy : globalPolicy.value)
const effectiveServerPolicy = computed(() => serverPolicy.value === 'inherit' ? effectiveGlobalPolicy.value : serverPolicy.value)

function policyLabel(policy) { return { disabled: 'Deaktiviert', ask: 'Nachfragen', auto: 'Automatisch' }[policy] || policy }
function inheritedOptions(policy) { return [{ label: `Erben: ${policyLabel(policy)}`, value: 'inherit' }, ...policyOptions] }
function catalogTool(resource) { return server.value?.tools?.find((tool) => tool.name === resource.label) }
function toolTitle(resource) { return catalogTool(resource)?.title || resource.label }
function toolDescription(resource) { return catalogTool(resource)?.description }
function annotationHints(resource) {
  const annotations = catalogTool(resource)?.annotations || {}
  return [
    typeof annotations.readOnlyHint === 'boolean' && (annotations.readOnlyHint ? 'Als nur lesend markiert' : 'Nicht als nur lesend markiert'),
    typeof annotations.destructiveHint === 'boolean' && (annotations.destructiveHint ? 'Als destruktiv markiert' : 'Als nicht destruktiv markiert'),
    typeof annotations.idempotentHint === 'boolean' && (annotations.idempotentHint ? 'Als wiederholbar markiert' : 'Nicht als wiederholbar markiert'),
    typeof annotations.openWorldHint === 'boolean' && (annotations.openWorldHint ? 'Greift laut Server auf externe Daten zu' : 'Laut Server ohne externe Daten')
  ].filter(Boolean)
}
function effectiveToolPolicy(resource) {
  const policy = toolPolicies[resource.key]
  return policy === 'inherit' ? effectiveServerPolicy.value : policy
}
function payload() {
  const policies = serverPolicy.value === 'inherit' ? {} : { 'mcp.call': serverPolicy.value }
  const resources = Object.fromEntries(Object.entries(toolPolicies)
    .filter(([, policy]) => policy !== 'inherit')
    .map(([key, policy]) => [key, { 'mcp.call': policy }]))
  return {
    ...(scope.value === 'instance' ? { globalPolicy: globalPolicy.value } : {}),
    policies,
    resources
  }
}
function assignAccess(value) {
  access.value = value
  globalPolicy.value = value.globalPolicy || value.effectiveGlobalPolicy
  const selectedAction = value.actions.find((item) => item.capability === 'mcp.call')
  serverPolicy.value = selectedAction?.integrationPolicy || 'inherit'
  for (const key of Object.keys(toolPolicies)) delete toolPolicies[key]
  for (const resource of value.resources) toolPolicies[resource.key] = resource.policies['mcp.call'] || 'inherit'
  savedPayload.value = JSON.stringify(payload())
}
async function load() {
  access.value = null
  savedPayload.value = ''
  servers.selectScope(scope.value)
  try {
    if (scope.value === 'personal') {
      await servers.fetchSettings()
      if (personalBlocked.value) return
    }
    if (!servers.loaded) await servers.fetchServers()
    const value = await servers.fetchAccess(serverId.value)
    if (value) assignAccess(value)
  } catch { /* Central API toast. */ }
}
async function save() {
  if (!dirty.value) return
  const value = await servers.saveAccess(serverId.value, payload())
  if (!value) return
  assignAccess(value)
  $q.notify({ type: 'positive', message: 'MCP-Zugriffsrichtlinien gespeichert' })
}
async function switchTrust(trust) {
  if (trust === servers.trust || !await confirmDiscardedChanges()) return
  servers.selectTrust(trust)
}
async function confirmDiscardedChanges() {
  if (servers.savingAccess) return false
  if (!dirty.value) return true
  return confirm('Sollen die ungespeicherten Änderungen verworfen werden?', 'Änderungen verwerfen?', { okLabel: 'Verwerfen' })
}
watch([serverId, scope, () => servers.trust], load, { immediate: true })
onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
