<template>
  <section :aria-busy="agents.loading">
    <q-toolbar class="q-px-none q-mb-md">
      <q-btn
        flat
        round
        dense
        icon="arrow_back"
        aria-label="Zurück zu den Agentenprofilen"
        :disable="agents.saving"
        :to="{ name: 'settings-agents', query: { scope } }"
      />
      <q-avatar color="primary" text-color="white" icon="smart_toy" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="row items-center q-gutter-sm">
          <div class="text-h6 text-weight-bold">
            {{ isCreate ? 'Agent erstellen' : (editingAgent?.name || 'Agent bearbeiten') }}
          </div>
          <q-badge v-if="dirty" color="warning" text-color="black" label="Ungespeichert" />
        </div>
        <div class="text-body2 app-muted">
          Persona, Anweisungen und sämtliche Tool-Rechte festlegen.
        </div>
      </q-toolbar-title>
      <q-btn
        unelevated
        no-caps
        color="primary"
        icon="save"
        :round="$q.screen.lt.sm"
        :label="$q.screen.gt.xs ? 'Speichern' : undefined"
        aria-label="Agentenprofil speichern"
        :loading="agents.saving"
        :disable="blocked || agents.loading || loadFailed || !dirty"
        @click="saveAgent"
      />
    </q-toolbar>

    <q-banner v-if="blocked" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="block" color="warning" /></template>
      Persönliche Agenten wurden für diese Instanz deaktiviert.
    </q-banner>

    <q-banner v-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      {{ notFound ? 'Das Agentenprofil wurde nicht gefunden.' : 'Das Agentenprofil konnte nicht geladen werden.' }}
      <template #action>
        <q-btn v-if="!notFound" flat no-caps color="white" label="Erneut laden" @click="load" />
        <q-btn v-else flat no-caps color="white" label="Zurück" :to="{ name: 'settings-agents', query: { scope } }" />
      </template>
    </q-banner>

    <q-form
      v-if="!loadFailed"
      ref="formRef"
      class="q-gutter-md"
      :class="{ disabled: blocked }"
      :inert="blocked"
      @submit.prevent="saveAgent"
    >
      <q-card flat class="app-surface">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Profil</div>
          <div class="text-caption app-muted">Name, Zweck und Verhältnis zur gemeinsamen Persona.</div>
        </q-card-section>
        <q-separator />
        <q-card-section class="q-gutter-md">
          <div class="row q-col-gutter-md">
            <div class="col-12 col-md-7">
              <q-input
                v-model="form.name"
                outlined
                autofocus
                maxlength="80"
                label="Name"
                :disable="isDefault"
                :rules="[requiredRule]"
                lazy-rules
              />
            </div>
            <div class="col-12 col-md-5">
              <q-select
                v-model="form.personaMode"
                outlined
                emit-value
                map-options
                label="Persona"
                :options="personaOptions"
              />
            </div>
          </div>
          <q-input
            v-model="form.description"
            outlined
            maxlength="500"
            autogrow
            type="textarea"
            label="Beschreibung"
          />
        </q-card-section>
      </q-card>

      <q-card flat class="app-surface">
        <q-card-section>
          <div class="text-subtitle1 text-weight-bold">Anweisungen</div>
          <div class="text-caption app-muted">Rolle, Arbeitsweise und gewünschte Ergebnisse dieses Agenten.</div>
        </q-card-section>
        <q-separator />
        <q-card-section>
          <q-input
            v-model="form.instructions"
            outlined
            type="textarea"
            autogrow
            maxlength="16000"
            label="Agentenanweisungen"
            :rules="[requiredRule]"
            lazy-rules
          />
        </q-card-section>
      </q-card>

      <div>
        <div class="text-h6 text-weight-bold">Tool-Rechte</div>
        <div class="text-body2 app-muted q-mb-md">
          Die technische Verfügbarkeit kommt aus den Tool- und Integrationseinstellungen. Dieses Profil legt fest, ob der Agent eine verfügbare Aktion ausführen darf.
        </div>

        <q-banner rounded class="app-surface-tonal q-mb-md">
          <template #avatar><q-icon name="privacy_tip" color="primary" /></template>
          Vererbte Werte folgen den wirksamen Tool- und Integrationsstandards. Private und öffentliche Richtlinien werden unabhängig gespeichert.
        </q-banner>

        <q-card
          v-for="group in permissionGroups"
          :key="group.id"
          flat
          class="app-surface q-mb-md"
        >
          <q-card-section class="row items-center q-gutter-md">
            <q-avatar color="primary" text-color="white" :icon="group.icon" />
            <div>
              <div class="text-subtitle1 text-weight-bold">{{ group.label }}</div>
              <div class="text-caption app-muted">Optionale Abweichungen von den wirksamen Tool-Standards.</div>
            </div>
          </q-card-section>
          <template v-for="action in group.actions" :key="action.capability">
            <q-separator />
            <q-card-section>
              <div class="row items-start q-col-gutter-md">
                <div class="col-12 col-md">
                  <div class="text-weight-medium">{{ action.label }}</div>
                  <div class="text-caption app-muted">{{ action.capability }}</div>
                  <q-chip v-if="action.policy === 'disabled'" dense outline color="warning" label="Tool deaktiviert" />
                </div>
                <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
                  <q-select
                    :model-value="globalPolicy(audience.value, action.capability)"
                    outlined
                    dense
                    emit-value
                    map-options
                    :label="audience.label"
                    :options="policyOptions(action, baselinePolicy(action.capability))"
                    :disable="action.policy === 'disabled'"
                    :hint="`Wirksam: ${policyLabel(effectiveGlobalPolicy(audience.value, action.capability))}`"
                    :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                    persistent-hint
                    @update:model-value="setGlobalPolicy(audience.value, action.capability, $event)"
                  />
                </div>
              </div>
            </q-card-section>
          </template>
        </q-card>

        <q-card flat class="app-surface">
          <q-card-section>
            <div class="text-subtitle1 text-weight-bold">Integrationen und Ressourcen</div>
            <div class="text-caption app-muted">
              Optional von Integrations- und Ressourcenstandards abweichen. Ressourcen werden erst beim Öffnen einer Integration geladen.
            </div>
          </q-card-section>
          <q-separator />
          <q-banner v-if="!policyIntegrations.length && !agents.loading" class="app-surface-tonal q-ma-md">
            Es sind keine aktivierbaren Integrationen konfiguriert.
          </q-banner>
          <q-list separator>
            <q-expansion-item
              v-for="integration in policyIntegrations"
              :key="integration.id"
              group="agent-integrations"
              :icon="integration.enabled ? 'extension' : 'extension_off'"
              :label="integration.name"
              :caption="integration.enabled ? 'Aktiv' : 'In den Integrationseinstellungen deaktiviert'"
              @show="loadIntegration(integration.id)"
            >
              <q-card-section v-if="agents.integrationMetadataFailed[integration.id]">
                <q-banner rounded class="app-surface-tonal">
                  Die Aktionen oder Ressourcen dieser Integration konnten nicht geladen werden.
                  <template #action>
                    <q-btn flat no-caps color="primary" label="Erneut laden" @click="loadIntegration(integration.id, true)" />
                  </template>
                </q-banner>
              </q-card-section>
              <q-card-section v-else-if="metadata(integration.id)" class="q-pa-none">
                <template v-for="action in metadata(integration.id).actions" :key="action.capability">
                  <q-separator />
                  <q-card-section>
                    <div class="row items-start q-col-gutter-md">
                      <div class="col-12 col-md">
                        <div class="text-weight-medium">{{ action.label }}</div>
                        <div class="text-caption app-muted">
                          Standard: {{ policyLabel(action.effectiveIntegrationPolicy) }}
                        </div>
                      </div>
                      <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
                        <q-select
                          :model-value="integrationPolicy(audience.value, integration.id, action.capability)"
                          outlined
                          dense
                          emit-value
                          map-options
                          :label="audience.label"
                          :disable="!integration.enabled || action.globalPolicy === 'disabled'"
                          :options="policyOptions(action, inheritedIntegrationPolicy(audience.value, action))"
                          :hint="`Wirksam: ${policyLabel(effectiveIntegrationPolicy(audience.value, integration.id, action.capability))}`"
                          :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                          persistent-hint
                          @update:model-value="setIntegrationPolicy(audience.value, integration.id, action.capability, $event)"
                        />
                      </div>
                    </div>
                  </q-card-section>
                </template>

                <q-expansion-item
                  v-for="resource in metadata(integration.id).resources"
                  :key="resource.key"
                  dense-toggle
                  icon="folder_open"
                  :label="resource.label"
                  header-class="app-surface-tonal"
                >
                  <template v-for="action in resourceActions(integration.id, resource)" :key="action.capability">
                    <q-separator />
                    <q-card-section>
                      <div class="row items-start q-col-gutter-md">
                        <div class="col-12 col-md">
                          <div class="text-weight-medium">{{ action.label }}</div>
                          <div class="text-caption app-muted">{{ resource.key }}</div>
                          <q-chip
                            v-if="resource.hardUnavailableCapabilities.includes(action.capability)"
                            dense
                            outline
                            color="warning"
                            label="Vom Dienst nicht erlaubt"
                          />
                        </div>
                        <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
                          <q-select
                            :model-value="resourcePolicy(audience.value, integration.id, resource.key, action.capability)"
                            outlined
                            dense
                            emit-value
                            map-options
                            :label="audience.label"
                            :disable="!integration.enabled || action.globalPolicy === 'disabled' || resource.hardUnavailableCapabilities.includes(action.capability)"
                            :options="policyOptions(action, inheritedResourcePolicy(audience.value, integration.id, resource, action.capability))"
                            :hint="resourcePolicyHint(audience.value, integration.id, resource, action.capability)"
                            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                            persistent-hint
                            @update:model-value="setResourcePolicy(audience.value, integration.id, resource.key, action.capability, $event)"
                          />
                        </div>
                      </div>
                    </q-card-section>
                  </template>
                </q-expansion-item>
              </q-card-section>
              <q-inner-loading :showing="agents.integrationMetadataLoading[integration.id]" />
            </q-expansion-item>
          </q-list>
        </q-card>
      </div>

      <div class="row justify-end q-gutter-sm q-pb-md">
        <q-btn flat no-caps label="Zurück" :disable="agents.saving" :to="{ name: 'settings-agents', query: { scope } }" />
        <q-btn
          unelevated
          no-caps
          type="submit"
          color="primary"
          icon="save"
          label="Speichern"
          :loading="agents.saving"
          :disable="agents.loading || loadFailed || !dirty"
        />
      </div>
    </q-form>

    <q-inner-loading :showing="agents.loading" />
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import { policyOverride, setPolicyOverride } from 'src/utils/agent-permissions'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'AgentProfileEditorPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const agents = useAgentProfileStore()
const auth = useAuthStore()
const formRef = ref(null)
const editingAgent = ref(null)
const loadFailed = ref(false)
const notFound = ref(false)
const savedPayload = ref('')

const emptyPermissions = () => ({ policies: {}, integrationPolicies: {}, resourcePolicies: {} })
const form = reactive({
  name: '',
  description: '',
  instructions: '',
  personaMode: 'extend',
  publicPermissions: emptyPermissions(),
  privatePermissions: emptyPermissions()
})
const audiences = [
  { label: 'Private Modelle', value: 'private' },
  { label: 'Öffentliche Modelle', value: 'public' }
]
const personaOptions = [
  { label: 'Gemeinsame Persona erweitern', value: 'extend' },
  { label: 'Gemeinsame Persona ersetzen', value: 'replace' }
]
const policyLabels = { disabled: 'Deaktiviert', ask: 'Nachfragen', auto: 'Automatisch' }
const clone = (value) => JSON.parse(JSON.stringify(value))
const scope = computed(() => resolveSettingsScope(route.query.scope, auth.isAdmin))
const isCreate = computed(() => route.name === 'settings-agent-create')
const isDefault = computed(() => editingAgent.value?.id === agents.defaultAgentProfileId)
const blocked = computed(() => scope.value === 'personal' && !agents.personalAgentsEnabled)
const dirty = computed(() => Boolean(savedPayload.value) && JSON.stringify(requestPayload()) !== savedPayload.value)
const policyIntegrations = computed(() => agents.integrations.filter((item) => item.supportsPolicies))
const baselinePolicies = computed(() => Object.fromEntries(
  agents.groups.flatMap((group) => (group.tools || []).flatMap((tool) => (
    (tool.actions || []).map((action) => [action.capability, action.policy])
  )))
))
const permissionGroups = computed(() => {
  const seen = new Set()
  return agents.groups.map((group) => ({
    id: group.id,
    label: group.label,
    icon: group.icon,
    actions: (group.tools || []).flatMap((tool) => (tool.actions || []).flatMap((action) => {
      if (!action.configurable || seen.has(action.capability)) return []
      seen.add(action.capability)
      return [{
        ...action,
        label: (tool.actions || []).filter((item) => item.configurable).length > 1
          ? `${tool.label}: ${action.label}`
          : tool.label
      }]
    }))
  })).filter((group) => group.actions.length)
})

const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
const permissionField = (audience) => `${audience}Permissions`
const permissions = (audience) => form[permissionField(audience)]
const policyLabel = (policy) => policyLabels[policy] || policy
const metadata = (integrationId) => agents.integrationMetadata[integrationId] || null

function requestPayload() {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    instructions: form.instructions.trim(),
    personaMode: form.personaMode,
    publicPermissions: clone(form.publicPermissions),
    privatePermissions: clone(form.privatePermissions)
  }
}

function assignForm(agent = null) {
  Object.assign(form, {
    name: agent?.name || '',
    description: agent?.description || '',
    instructions: agent?.instructions || '',
    personaMode: agent?.personaMode === 'replace' ? 'replace' : 'extend',
    publicPermissions: clone(agent?.publicPermissions || emptyPermissions()),
    privatePermissions: clone(agent?.privatePermissions || emptyPermissions())
  })
  savedPayload.value = JSON.stringify(requestPayload())
}

function globalPolicy(audience, capability) {
  return policyOverride(permissions(audience).policies, [capability])
}

function integrationPolicy(audience, integrationId, capability) {
  return policyOverride(permissions(audience).integrationPolicies, [integrationId, capability])
}

function resourcePolicy(audience, integrationId, resourceKey, capability) {
  return policyOverride(permissions(audience).resourcePolicies, [integrationId, resourceKey, capability])
}

function baselinePolicy(capability) {
  return baselinePolicies.value[capability] || 'disabled'
}

function effectiveGlobalPolicy(audience, capability) {
  if (baselinePolicy(capability) === 'disabled') return 'disabled'
  const configured = globalPolicy(audience, capability)
  return configured === 'inherit' ? baselinePolicy(capability) : configured
}

function effectiveIntegrationPolicy(audience, integrationId, capability) {
  if (!policyIntegrations.value.find((item) => item.id === integrationId)?.enabled) return 'disabled'
  const action = metadata(integrationId)?.actions.find((item) => item.capability === capability)
  if (action?.globalPolicy === 'disabled') return 'disabled'
  const configured = integrationPolicy(audience, integrationId, capability)
  if (configured !== 'inherit') return configured
  const global = globalPolicy(audience, capability)
  if (global !== 'inherit') return global
  return action?.effectiveIntegrationPolicy || baselinePolicy(capability)
}

function inheritedIntegrationPolicy(audience, action) {
  if (action.globalPolicy === 'disabled') return 'disabled'
  const global = globalPolicy(audience, action.capability)
  return global === 'inherit' ? action.effectiveIntegrationPolicy : global
}

function inheritedResourcePolicy(audience, integrationId, resource, capability) {
  const action = metadata(integrationId)?.actions.find((item) => item.capability === capability)
  if (action?.globalPolicy === 'disabled' || resource.hardUnavailableCapabilities.includes(capability)) return 'disabled'
  const integration = integrationPolicy(audience, integrationId, capability)
  if (integration !== 'inherit') return integration
  const global = globalPolicy(audience, capability)
  if (global !== 'inherit') return global
  return resource.effectivePolicies?.[capability]
    || action?.effectiveIntegrationPolicy
    || baselinePolicy(capability)
}

function policyOptions(action, inherited) {
  return [
    { label: `Erben: ${policyLabel(inherited)}`, value: 'inherit' },
    ...(action.allowedPolicies || []).map((value) => ({ label: policyLabel(value), value }))
  ]
}

function setGlobalPolicy(audience, capability, policy) {
  setPolicyOverride(permissions(audience).policies, [capability], policy)
}

function setIntegrationPolicy(audience, integrationId, capability, policy) {
  setPolicyOverride(
    permissions(audience).integrationPolicies,
    [integrationId, capability],
    policy
  )
}

function setResourcePolicy(audience, integrationId, resourceKey, capability, policy) {
  setPolicyOverride(
    permissions(audience).resourcePolicies,
    [integrationId, resourceKey, capability],
    policy
  )
}

function resourcePolicyHint(audience, integrationId, resource, capability) {
  if (!policyIntegrations.value.find((item) => item.id === integrationId)?.enabled) return 'Wirksam: Deaktiviert'
  const action = metadata(integrationId)?.actions.find((item) => item.capability === capability)
  if (action?.globalPolicy === 'disabled' || resource.hardUnavailableCapabilities.includes(capability)) return 'Wirksam: Deaktiviert'
  const configured = resourcePolicy(audience, integrationId, resource.key, capability)
  const effective = configured === 'inherit'
    ? inheritedResourcePolicy(audience, integrationId, resource, capability)
    : configured
  return `Wirksam: ${policyLabel(effective)}`
}

function resourceActions(integrationId, resource) {
  return (metadata(integrationId)?.actions || []).filter((action) => (
    resource.applicableCapabilities.includes(action.capability)
  ))
}

async function loadIntegration(integrationId, force = false) {
  if (!force && (metadata(integrationId) || agents.integrationMetadataLoading[integrationId])) return
  try {
    await agents.fetchIntegrationMetadata(integrationId)
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function load() {
  editingAgent.value = null
  savedPayload.value = ''
  loadFailed.value = false
  notFound.value = false
  agents.selectScope(scope.value)
  agents.resetIntegrationMetadata()
  try {
    await agents.fetchAgents(scope.value)
    if (isCreate.value) {
      editingAgent.value = null
      assignForm()
      return
    }
    const agent = agents.byId(String(route.params.agentId || ''))
    if (!agent || agent.scope !== scope.value) {
      notFound.value = true
      loadFailed.value = true
      return
    }
    editingAgent.value = agent
    assignForm(agent)
  } catch {
    loadFailed.value = true
  }
}

async function saveAgent() {
  if (agents.loading || loadFailed.value || !dirty.value) return
  if (!await formRef.value?.validate()) return
  const creating = isCreate.value
  try {
    const saved = editingAgent.value
      ? await agents.updateAgent(editingAgent.value.id, requestPayload())
      : await agents.createAgent(requestPayload())
    editingAgent.value = saved
    assignForm(saved)
    if (creating) {
      await router.replace({
        name: 'settings-agent-edit',
        params: { agentId: saved.id },
        query: { scope: scope.value }
      })
    }
    $q.notify({ type: 'positive', message: creating ? 'Agent erstellt' : 'Agent gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function confirmDiscardedChanges() {
  if (agents.saving) return false
  if (!dirty.value) return true
  return confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
}

onMounted(load)
watch(
  () => [route.name, route.params.agentId, route.query.scope],
  () => void load()
)
onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
