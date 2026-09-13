<template>
  <section :aria-busy="agents.loading">
    <q-toolbar class="settings-detail-toolbar q-px-none q-mb-md">
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
          Persona, Aufgaben-Prompt und Berechtigungen festlegen.
        </div>
      </q-toolbar-title>
      <div class="settings-scope-actions row items-center col-auto">
        <SettingsScopeToggle
          :model-value="scope"
          :is-admin="auth.isAdmin"
          :disable="agents.loading || agents.saving"
          @update:model-value="switchScope"
        />
        <q-btn
          class="col-auto"
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="save"
          label="Speichern"
          aria-label="Agentenprofil speichern"
          :loading="agents.saving"
          :disable="blocked || agents.loading || loadFailed || !dirty"
          @click="saveAgent"
        />
      </div>
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
      :class="{ disabled: blocked }"
      :inert="blocked"
      @submit.prevent="saveAgent"
    >
      <q-tabs
        v-model="section"
        dense
        no-caps
        align="left"
        active-color="primary"
        indicator-color="primary"
      >
        <q-tab name="profile" icon="badge" label="Profil" />
        <q-tab name="tools" icon="build" label="Tools" />
        <q-tab name="integrations" icon="extension" label="Integrationen" />
      </q-tabs>
      <q-separator class="q-mb-lg" />

      <q-tab-panels
        v-model="section"
        animated
        transition-prev="fade"
        transition-next="fade"
        class="bg-transparent"
      >
        <q-tab-panel name="profile" class="q-pa-none">
          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <div class="text-subtitle1 text-weight-bold">Profil</div>
              <div class="text-caption app-muted">Name und Zweck dieses Agentenprofils.</div>
            </q-card-section>
            <q-separator />
            <q-card-section>
              <q-input
                v-model="form.name"
                outlined
                autofocus
                maxlength="80"
                label="Name"
                :disable="isProtected"
                :rules="[requiredRule]"
                lazy-rules
              />
              <q-input
                v-model="form.description"
                class="q-mt-md"
                outlined
                maxlength="500"
                autogrow
                type="textarea"
                label="Beschreibung"
              />
              <q-select
                v-model="form.answerDisplay"
                class="q-mt-md"
                outlined
                emit-value
                map-options
                label="Antwortdarstellung"
                :options="answerDisplayOptions"
                :hint="answerDisplayOptions.find((option) => option.value === form.answerDisplay)?.hint"
                persistent-hint
              />
              <q-toggle
                v-model="form.todoEnabled"
                class="q-mt-md"
                label="Session-Aufgaben verwenden"
              />
              <div class="text-caption app-muted">
                Erlaubt dem Agenten, eine Aufgabenliste für längere Arbeiten zu führen.
              </div>
            </q-card-section>
          </q-card>

          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <div class="text-subtitle1 text-weight-bold">Tool-Aufrufe pro Lauf</div>
              <q-toggle
                :model-value="form.maxToolCalls === null"
                label="Unbegrenzt"
                @update:model-value="form.maxToolCalls = $event ? null : 25"
              />
              <q-input
                v-if="form.maxToolCalls !== null"
                v-model.number="form.maxToolCalls"
                outlined
                type="number"
                min="1"
                step="1"
                label="Maximale Tool-Aufrufe"
                :rules="[toolLimitRule]"
                lazy-rules
              />
              <q-banner rounded class="app-surface-tonal q-mt-sm">
                <template #avatar><q-icon name="warning_amber" color="warning" /></template>
                Hohe oder unbegrenzte Limits können lange Läufe und hohe Modell- und Dienstkosten verursachen.
                Ohne Limit gibt es keinen automatischen Stopp durch die Anzahl der Tool-Aufrufe.
              </q-banner>
            </q-card-section>
          </q-card>

          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <div class="text-subtitle1 text-weight-bold">Persona</div>
              <div class="text-caption app-muted">Wiederverwendbares Verhalten und Charakter dieses Agenten.</div>
            </q-card-section>
            <q-separator />
            <q-card-section>
              <q-select
                v-model="form.personaId"
                outlined
                emit-value
                map-options
                clearable
                label="Persona"
                :options="personaOptions"
                hint="Ohne Auswahl gilt die Standard-Persona dieses Bereichs."
              />
              <q-btn
                flat
                no-caps
                color="primary"
                icon="manage_accounts"
                label="Personas verwalten"
                class="q-mt-sm"
                :to="{ name: 'settings-personalization', query: { tab: 'assistant', scope } }"
              />
            </q-card-section>
          </q-card>

          <q-card flat class="app-surface">
            <q-card-section>
              <div class="text-subtitle1 text-weight-bold">Aufgaben-Prompt</div>
              <div class="text-caption app-muted">Konkrete Aufgaben, Vorgehen und gewünschte Ergebnisse dieses Agenten.</div>
            </q-card-section>
            <q-separator />
            <q-card-section>
              <q-input
                v-model="form.instructions"
                outlined
                type="textarea"
                autogrow
                maxlength="16000"
                label="Aufgabenanweisungen"
                :rules="[requiredRule]"
                lazy-rules
              />
            </q-card-section>
          </q-card>
        </q-tab-panel>

        <q-tab-panel name="tools" class="q-pa-none">
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
                    <q-chip
                      v-if="action.available === false"
                      dense
                      outline
                      color="warning"
                      label="Technisch deaktiviert"
                    />
                  </div>
                  <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
                    <q-select
                      :model-value="globalPolicy(audience.value, action.capability)"
                      outlined
                      dense
                      emit-value
                      map-options
                      :label="audience.label"
                      :options="policyOptions(baselinePolicy(audience.value, action.capability))"
                      :disable="action.available === false"
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
        </q-tab-panel>

        <q-tab-panel name="integrations" class="q-pa-none">
          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <q-toggle
                v-model="form.integrationsEnabledByDefault"
                label="Integrationen standardmäßig aktivieren"
              />
              <div class="text-body2 app-muted">
                {{ form.integrationsEnabledByDefault
                  ? 'Auch künftig konfigurierte Integrationen sind für dieses Profil verfügbar.'
                  : 'Nur ausdrücklich aktivierte Integrationen sind für dieses Profil verfügbar.' }}
                Einzelne Abweichungen bleiben beim Umschalten erhalten. Die Auswahl erteilt keine Berechtigungen.
              </div>
            </q-card-section>
          </q-card>
          <q-card flat class="app-surface">
            <q-card-section>
              <div class="text-subtitle1 text-weight-bold">Integrationen und Ressourcen</div>
              <div class="text-caption app-muted">
                Optional von Integrations- und Ressourcenstandards abweichen. Ressourcen werden erst beim Öffnen einer Integration geladen.
              </div>
            </q-card-section>
            <q-separator />
            <q-banner v-if="!agents.integrations.length && !agents.loading" class="app-surface-tonal q-ma-md">
              Es sind keine aktivierbaren Integrationen konfiguriert.
            </q-banner>
            <q-list separator>
              <q-expansion-item
                v-for="integration in agents.integrations"
                :key="integration.id"
                group="agent-integrations"
                :icon="integration.enabled ? 'extension' : 'extension_off'"
                :label="integration.name"
                :caption="integrationHint(integration)"
                @show="loadIntegration(integration.id)"
              >
                <q-card-section>
                  <q-select
                    :model-value="form.integrationAvailability[integration.id] ?? 'inherit'"
                    outlined
                    emit-value
                    map-options
                    :label="`Verfügbarkeit: ${integration.name}`"
                    :options="integrationAvailabilityOptions"
                    :hint="integrationHint(integration)"
                    :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                    persistent-hint
                    @update:model-value="setIntegrationAvailability(integration.id, $event)"
                  />
                  <div v-if="!integration.supportsPolicies" class="text-caption app-muted q-mt-sm">
                    Diese Integration stellt Optionen oder Projektfunktionen bereit. Deren bestehende Tool-Rechte gelten weiterhin.
                  </div>
                </q-card-section>
                <q-card-section v-if="agents.integrationMetadataFailed[integration.id]">
                  <q-banner rounded class="app-surface-tonal">
                    Die Aktionen oder Ressourcen dieser Integration konnten nicht geladen werden.
                    <template #action>
                      <q-btn flat no-caps color="primary" label="Erneut laden" @click="loadIntegration(integration.id, true)" />
                    </template>
                  </q-banner>
                </q-card-section>
                <q-card-section v-else-if="metadata(integration.id)" class="q-pa-none">
                  <template v-for="action in displayMetadata(integration.id).actions" :key="action.capability">
                    <q-separator />
                    <q-card-section>
                      <div class="row items-start q-col-gutter-md">
                        <div class="col-12 col-md">
                          <div class="text-weight-medium">{{ action.label }}</div>
                          <div class="text-caption app-muted">Vererbt den Standard der jeweiligen Modellklasse.</div>
                        </div>
                        <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
                          <q-select
                            :model-value="integrationPolicy(audience.value, integration.id, action.capability)"
                            outlined
                            dense
                            emit-value
                            map-options
                            :label="audience.label"
                            :disable="!integrationAvailable(integration)"
                            :options="policyOptions(inheritedIntegrationPolicy(audience.value, integration.id, action))"
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
                    v-for="resource in displayMetadata(integration.id).resources"
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
                              :disable="!integrationAvailable(integration) || resource.hardUnavailableCapabilities.includes(action.capability)"
                              :options="policyOptions(inheritedResourcePolicy(audience.value, integration.id, resource, action.capability))"
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
        </q-tab-panel>
      </q-tab-panels>

      <div class="row justify-end q-gutter-sm q-py-md">
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
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
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
const section = ref('profile')

const emptyPermissions = () => ({ policies: {}, integrationPolicies: {}, resourcePolicies: {} })
const form = reactive({
  name: '',
  description: '',
  personaId: null,
  instructions: '',
  answerDisplay: 'compact',
  maxToolCalls: 25,
  todoEnabled: false,
  integrationsEnabledByDefault: true,
  integrationAvailability: {},
  publicPermissions: emptyPermissions(),
  privatePermissions: emptyPermissions()
})
const audiences = [
  { label: 'Private Modelle', value: 'private' },
  { label: 'Öffentliche Modelle', value: 'public' }
]
const answerDisplayOptions = [
  { label: 'Ablauf', value: 'timeline', hint: 'Zeigt alle Arbeitsschritte direkt in zeitlicher Reihenfolge.' },
  { label: 'Kompakt', value: 'compact', hint: 'Zeigt die aktuelle Aktivität sofort und den bisherigen Verlauf auf Wunsch.' }
]
const scope = computed(() => resolveSettingsScope(route.query.scope, auth.isAdmin))
const personaOptions = computed(() => [
  { label: 'Standard-Persona', value: null },
  ...agents.personas
    .filter((persona) => scope.value === 'personal' || persona.scope === 'instance')
    .map((persona) => ({
      label: `${persona.name} (${persona.scope === 'instance' ? 'Instanz' : 'Persönlich'})`,
      value: persona.id,
      disable: persona.effective === false
    }))
])
const policyLabels = { disabled: 'Deaktiviert', ask: 'Nachfragen', auto: 'Automatisch' }
const clone = (value) => JSON.parse(JSON.stringify(value))
const isCreate = computed(() => route.name === 'settings-agent-create')
const isProtected = computed(() => editingAgent.value?.protected === true)
const blocked = computed(() => scope.value === 'personal' && !agents.personalAgentsEnabled)
const dirty = computed(() => Boolean(savedPayload.value) && JSON.stringify(requestPayload()) !== savedPayload.value)
const integrationAvailabilityOptions = computed(() => [
  { label: `Standard (${form.integrationsEnabledByDefault ? 'Aktiv' : 'Deaktiviert'})`, value: 'inherit' },
  { label: 'Aktiv', value: true },
  { label: 'Deaktiviert', value: false }
])
const baselineActions = computed(() => Object.fromEntries(audiences.map(({ value }) => [
  value,
  Object.fromEntries((agents.groupsByTrust[value] || []).flatMap((group) => (
    (group.tools || []).flatMap((tool) => (
      (tool.actions || []).map((action) => [action.capability, {
        ...action, available: tool.available !== false && action.available !== false
      }])
    ))
  )))
])))
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
        available: tool.available !== false && action.available !== false,
        label: (tool.actions || []).filter((item) => item.configurable).length > 1
          ? `${tool.label}: ${action.label}`
          : tool.label
      }]
    }))
  })).filter((group) => group.actions.length)
})

const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
const toolLimitRule = (value) => (Number.isSafeInteger(value) && value > 0) || 'Eine positive ganze Zahl eingeben.'
const permissionField = (audience) => `${audience}Permissions`
const permissions = (audience) => form[permissionField(audience)]
const policyLabel = (policy) => policyLabels[policy] || policy
const metadata = (integrationId) => agents.integrationMetadata[integrationId] || null
const displayMetadata = (integrationId) => {
  const value = metadata(integrationId)
  return value?.private || value
}

function audienceMetadata(integrationId, audience) {
  const value = metadata(integrationId)
  return value?.[audience] || value
}

function audienceAction(audience, integrationId, capability) {
  return audienceMetadata(integrationId, audience)?.actions?.find((item) => (
    item.capability === capability
  ))
}

function audienceResource(audience, integrationId, resourceKey) {
  return audienceMetadata(integrationId, audience)?.resources?.find((item) => (
    item.key === resourceKey
  ))
}

function integrationAvailable(integration) {
  return Boolean(integration?.enabled && integration.available !== false
    && (form.integrationAvailability[integration.id] ?? form.integrationsEnabledByDefault))
}

function integrationHint(integration) {
  if (!integration.enabled || integration.available === false) return 'Technisch nicht verfügbar; die Profilauswahl bleibt gespeichert.'
  return integrationAvailable(integration)
    ? 'Für dieses Profil verfügbar; Tool-Rechte gelten zusätzlich.'
    : 'Für dieses Profil deaktiviert; gespeicherte Tool-Rechte bleiben erhalten.'
}

function setIntegrationAvailability(integrationId, value) {
  if (value === 'inherit') delete form.integrationAvailability[integrationId]
  else form.integrationAvailability[integrationId] = value
}

function requestPayload() {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    personaId: form.personaId,
    instructions: form.instructions.trim(),
    answerDisplay: form.answerDisplay,
    maxToolCalls: form.maxToolCalls,
    todoEnabled: form.todoEnabled,
    integrationsEnabledByDefault: form.integrationsEnabledByDefault,
    integrationAvailability: clone(form.integrationAvailability),
    publicPermissions: clone(form.publicPermissions),
    privatePermissions: clone(form.privatePermissions)
  }
}

function assignForm(agent = null) {
  Object.assign(form, {
    name: agent?.name || '',
    description: agent?.description || '',
    personaId: agent?.personaId || null,
    instructions: agent?.instructions || '',
    answerDisplay: agent?.answerDisplay === 'timeline' ? 'timeline' : 'compact',
    maxToolCalls: agent?.maxToolCalls === null ? null : (agent?.maxToolCalls ?? 25),
    todoEnabled: agent?.todoEnabled ?? false,
    integrationsEnabledByDefault: agent?.integrationsEnabledByDefault ?? true,
    integrationAvailability: clone(agent?.integrationAvailability || {}),
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

function baselinePolicy(audience, capability) {
  return baselineActions.value[audience]?.[capability]?.policy || 'disabled'
}

function effectiveGlobalPolicy(audience, capability) {
  if (baselineActions.value[audience]?.[capability]?.available === false) return 'disabled'
  const configured = globalPolicy(audience, capability)
  return configured === 'inherit' ? baselinePolicy(audience, capability) : configured
}

function effectiveIntegrationPolicy(audience, integrationId, capability) {
  if (!integrationAvailable(agents.integrations.find((item) => item.id === integrationId))) return 'disabled'
  const action = audienceAction(audience, integrationId, capability)
  const configured = integrationPolicy(audience, integrationId, capability)
  if (configured !== 'inherit') return configured
  const global = globalPolicy(audience, capability)
  if (global !== 'inherit') return global
  return action?.effectiveIntegrationPolicy
    || baselinePolicy(audience, capability)
}

function inheritedIntegrationPolicy(audience, integrationId, action) {
  const branchAction = audienceAction(audience, integrationId, action.capability) || action
  const global = globalPolicy(audience, action.capability)
  return global === 'inherit'
    ? branchAction.effectiveIntegrationPolicy
      || baselinePolicy(audience, action.capability)
    : global
}

function inheritedResourcePolicy(audience, integrationId, resource, capability) {
  const action = audienceAction(audience, integrationId, capability)
  const branchResource = audienceResource(audience, integrationId, resource.key) || resource
  if (resource.hardUnavailableCapabilities.includes(capability)) return 'disabled'
  const integration = integrationPolicy(audience, integrationId, capability)
  if (integration !== 'inherit') return integration
  const global = globalPolicy(audience, capability)
  if (global !== 'inherit') return global
  return branchResource.effectivePolicies?.[capability]
    || action?.effectiveIntegrationPolicy
    || baselinePolicy(audience, capability)
}

function policyOptions(inherited) {
  return [
    { label: `Erben: ${policyLabel(inherited)}`, value: 'inherit' },
    ...['disabled', 'ask', 'auto'].map((value) => ({ label: policyLabel(value), value }))
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
  if (!integrationAvailable(agents.integrations.find((item) => item.id === integrationId))) return 'Wirksam: Deaktiviert'
  if (resource.hardUnavailableCapabilities.includes(capability)) return 'Wirksam: Deaktiviert'
  const configured = resourcePolicy(audience, integrationId, resource.key, capability)
  const effective = configured === 'inherit'
    ? inheritedResourcePolicy(audience, integrationId, resource, capability)
    : configured
  return `Wirksam: ${policyLabel(effective)}`
}

function resourceActions(integrationId, resource) {
  return (displayMetadata(integrationId)?.actions || []).filter((action) => (
    resource.applicableCapabilities.includes(action.capability)
  ))
}

async function loadIntegration(integrationId, force = false) {
  if (!agents.integrations.find((item) => item.id === integrationId)?.supportsPolicies) return
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
  const invalidLimit = form.maxToolCalls !== null && toolLimitRule(form.maxToolCalls) !== true
  if (!form.name.trim() || !form.instructions.trim() || invalidLimit) {
    section.value = 'profile'
    await nextTick()
  }
  if (!await formRef.value?.validate()) return
  if (invalidLimit) return
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

async function switchScope(value) {
  const failure = await router.push({ name: 'settings-agents', query: { scope: value } })
  if (failure) return
  agents.selectScope(value)
  await agents.fetchAgents(value).catch(() => {})
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
