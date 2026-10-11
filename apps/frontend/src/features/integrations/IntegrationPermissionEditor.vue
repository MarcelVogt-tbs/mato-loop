<template>
  <div v-if="resources.some((resource) => resource.toolName)" class="text-caption app-muted q-mb-md">Tool-Metadaten stammen vom Server und sind nicht verifiziert. Sie bestimmen keine Berechtigungen.</div>
  <div class="row justify-end q-gutter-sm q-mb-md">
    <q-btn v-if="profileId" flat round dense icon="delete_outline" aria-label="Alle Overrides löschen" color="negative" :disable="disabled || !hasOverrides" @click="removeAll"><q-tooltip>Alle Overrides löschen</q-tooltip></q-btn>
    <q-btn v-if="profileId || resources.length" flat no-caps icon="add" color="primary" label="Override hinzufügen" :disable="disabled" @click="edit()" />
  </div>
  <q-card v-if="profileId && permissions.available != null" flat class="app-surface q-mb-md">
    <q-card-section class="row items-center q-gutter-sm">
      <div class="col">Integration für dieses Profil</div>
      <q-toggle v-model="permissions.available" dense :disable="disabled" aria-label="Integration für dieses Profil verfügbar" />
      <q-btn flat round dense icon="delete_outline" aria-label="Verfügbarkeits-Override entfernen" :disable="disabled" @click="removeAvailability()" />
    </q-card-section>
  </q-card>
  <q-card v-if="profileId && !hasVisibleOverrides" flat class="app-surface q-mb-md"><q-card-section class="app-muted">Keine eigenen Overrides. Es gelten die Vorgaben dieser Integration.</q-card-section></q-card>
  <PermissionFields v-model="permissions" :groups-by-trust="groups" :resources="visibleResources" :inherited-resources="inheritedResources" :model-trust-enabled="modelTrustEnabled" :overrides-only="Boolean(profileId)" :readonly="Boolean(profileId)" resource-overrides-only :disabled="disabled">
    <template v-if="profileId" #action-controls="{ action }">
      <div class="col-auto row no-wrap">
        <q-btn flat round dense icon="edit" :aria-label="`${action.label} bearbeiten`" :disable="disabled" @click="edit('', action.capability)" />
        <q-btn flat round dense icon="delete_outline" :aria-label="`${action.label} entfernen`" :disable="disabled" @click="removePolicy('', action.capability)" />
      </div>
    </template>
    <template #resource-controls="{ resource }">
      <div class="row items-center no-wrap">
        <q-toggle dense :model-value="resource.available" :aria-label="`${resource.label}: verfügbar`" :disable="disabled" @update:model-value="permissions.resourceAvailability[resource.key] = $event" />
        <q-btn flat round dense icon="add" :aria-label="`${resource.label}: Override hinzufügen`" :disable="disabled" @click="edit(resource.key)" />
        <q-btn v-if="Object.hasOwn(permissions.resourceAvailability, resource.key)" flat round dense icon="undo" :aria-label="`${resource.label}: Verfügbarkeit erben`" :disable="disabled" @click="removeAvailability(resource.key)" />
      </div>
    </template>
    <template #resource-action-controls="{ resource, action }">
      <div class="col-auto row no-wrap">
        <q-btn flat round dense icon="edit" :aria-label="`${resource.label}: ${action.label} bearbeiten`" :disable="disabled" @click="edit(resource.key, action.capability)" />
        <q-btn flat round dense icon="delete_outline" :aria-label="`${resource.label}: ${action.label} entfernen`" :disable="disabled" @click="removePolicy(resource.key, action.capability)" />
      </div>
    </template>
  </PermissionFields>
  <q-dialog v-model="open" :maximized="$q.screen.lt.sm" aria-labelledby="integration-override-title">
    <q-card class="app-surface column no-wrap full-width">
      <q-card-section class="row items-center"><div id="integration-override-title" class="text-h6 col">Override {{ editing ? 'bearbeiten' : 'hinzufügen' }}</div><q-btn v-close-popup flat round dense icon="close" aria-label="Dialog schließen" /></q-card-section>
      <q-card-section class="col scroll q-gutter-md">
        <AppSelect v-model="draft.resource" label="Bereich" outlined emit-value map-options use-input :input-debounce="0" :options="targetOptions" :disable="editing" @filter="filterTargets" @update:model-value="draft.capability = null" />
        <div v-if="selectedResource?.toolName" class="text-body2 app-muted">
          <div v-if="selectedResource.toolName !== selectedResource.label" class="text-caption settings-list-name">{{ selectedResource.toolName }}</div>
          <div v-if="selectedResource.description" class="settings-list-name">{{ selectedResource.description }}</div>
          <div v-if="selectedResource.hints?.length" class="text-caption">{{ selectedResource.hints.join(' · ') }}</div>
        </div>
        <AppSelect v-model="draft.capability" label="Aktion" outlined emit-value map-options :options="actionOptions" :disable="editing" />
        <AppSelect v-if="draft.capability === 'availability'" v-model="draft.available" label="Verfügbarkeit" outlined emit-value map-options :options="[{ label: 'Vorgabe übernehmen', value: null }, { label: 'Verfügbar', value: true }, { label: 'Nicht verfügbar', value: false }]" />
        <template v-else-if="draft.capability">
          <AppSelect v-for="trust in audiences" :key="trust" v-model="draft[trust]" outlined emit-value map-options :label="modelTrustEnabled ? trust === 'private' ? 'Private Modelle' : 'Öffentliche Modelle' : 'Alle Modelle'" :options="policyOptions" :hint="`Geerbte Vorgabe: ${policyLabels[inheritedPolicy(trust)]}`" persistent-hint />
        </template>
        <div class="text-caption app-muted">Übernehmen aktualisiert das Formular. Anschließend die Seite speichern.</div>
      </q-card-section>
      <q-card-actions align="right"><q-btn v-close-popup flat no-caps label="Abbrechen" /><q-btn color="primary" unelevated no-caps label="Übernehmen" :disable="disabled || !draft.capability" @click="apply" /></q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useConfirm } from 'src/composables/confirm'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import PermissionFields from 'src/features/settings/PermissionFields.vue'
import { policyOverride, setPolicyOverride } from 'src/utils/agent-permissions'

const permissions = defineModel({ type: Object, required: true })
const props = defineProps({ data: { type: Object, required: true }, profileId: { type: String, default: null }, modelTrustEnabled: Boolean, disabled: Boolean })
const $q = useQuasar()
const { confirm } = useConfirm()
const policyLabels = { inherit: 'Vorgabe übernehmen', auto: 'Automatisch', ask: 'Nachfragen', disabled: 'Deaktiviert' }
const audiences = computed(() => props.modelTrustEnabled ? ['private', 'public'] : ['private'])
const branch = (trust) => permissions.value[`${trust}Permissions`]
const groups = computed(() => Object.fromEntries(['private', 'public'].map((trust) => [trust, [{ id: 'integration', label: 'Berechtigungen', icon: 'security', tools: [], actions: (props.data.inherited[trust]?.actions || []).map((action) => ({ ...action, policy: action.effectiveIntegrationPolicy, available: !props.profileId || (permissions.value.available ?? props.data.inheritedAvailable) })) }]])))
const resources = computed(() => props.data.inherited.private?.resources || [])
const inheritedResources = computed(() => Object.fromEntries(['private', 'public'].map((trust) => [trust, props.data.inherited[trust]?.resources || []])))
const hasOverrides = computed(() => permissions.value.available != null || Object.keys(permissions.value.resourceAvailability).length || ['private', 'public'].some((trust) => Object.keys(branch(trust).policies).length || Object.keys(branch(trust).resourcePolicies).length))
const resourceOverridden = (key) => permissions.value.resourceAvailability[key] != null || audiences.value.some((trust) => Object.keys(branch(trust).resourcePolicies[key] || {}).length)
const visibleResources = computed(() => resources.value.filter((resource) => !props.profileId || resourceOverridden(resource.key)).map((resource) => ({ ...resource, available: permissions.value.resourceAvailability[resource.key] ?? resource.available ?? true })))
const hasVisibleOverrides = computed(() => permissions.value.available != null || visibleResources.value.length || audiences.value.some((trust) => Object.keys(branch(trust).policies).length))
const open = ref(false)
const editing = ref(false)
const draft = ref({ resource: '', capability: null, available: true, private: 'inherit', public: 'inherit' })
const search = ref('')
const selectedResource = computed(() => resources.value.find((resource) => resource.key === draft.value.resource))
const targetOptions = computed(() => [...(props.profileId ? [{ label: 'Integration', value: '' }] : []), ...resources.value.map((resource) => ({ label: resource.label, value: resource.key, caption: resource.toolName !== resource.label ? resource.toolName : undefined }))]
  .filter((option) => `${option.label} ${option.caption || ''}`.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())))
function filterTargets(value, update) {
  update(() => { search.value = value })
}
const actionOptions = computed(() => {
  const resource = resources.value.find((item) => item.key === draft.value.resource)
  const availability = draft.value.resource ? permissions.value.resourceAvailability[draft.value.resource] : permissions.value.available
  return [...(editing.value || availability == null ? [{ label: 'Verfügbarkeit', value: 'availability' }] : []), ...(groups.value.private[0].actions || []).filter((action) => (!resource?.applicableCapabilities || resource.applicableCapabilities.includes(action.capability)) && (editing.value || !audiences.value.some((trust) => policyOverride(policies(trust, draft.value.resource), path(draft.value.resource, action.capability)) !== 'inherit'))).map((action) => ({ label: action.label, value: action.capability }))]
})
const policyOptions = computed(() => ['inherit', ...(groups.value.private[0].actions.find((action) => action.capability === draft.value.capability)?.allowedPolicies || ['disabled', 'ask', 'auto'])].map((value) => ({ label: policyLabels[value], value })))
const path = (resource, capability) => resource ? [resource, capability] : [capability]
const policies = (trust, resource) => resource ? branch(trust).resourcePolicies : branch(trust).policies
function inheritedPolicy(trust) {
  const { resource, capability } = draft.value
  const action = groups.value[trust][0].actions.find((item) => item.capability === capability)
  return resource ? inheritedResources.value[trust].find((item) => item.key === resource)?.policies?.[capability] || branch(trust).policies[capability] || action?.policy || 'ask' : action?.policy || 'ask'
}
function edit(resource = props.profileId ? '' : resources.value[0]?.key, capability = null) {
  search.value = ''
  editing.value = Boolean(capability)
  draft.value = { resource, capability, available: resource ? permissions.value.resourceAvailability[resource] ?? null : permissions.value.available ?? null,
    ...Object.fromEntries(audiences.value.map((trust) => [trust, capability ? policyOverride(policies(trust, resource), path(resource, capability)) : 'inherit'])) }
  open.value = true
}
function apply() {
  const { resource, capability, available } = draft.value
  if (capability === 'availability') {
    if (!resource) permissions.value.available = available
    else if (available == null) delete permissions.value.resourceAvailability[resource]
    else permissions.value.resourceAvailability[resource] = available
  } else for (const trust of audiences.value) setPolicyOverride(policies(trust, resource), path(resource, capability), draft.value[trust])
  open.value = false
}
async function removePolicy(resource, capability) {
  if (!await confirm('Diesen Berechtigungs-Override entfernen und die Vorgabe wieder übernehmen?', 'Override entfernen?', { okLabel: 'Entfernen' })) return
  for (const trust of audiences.value) setPolicyOverride(policies(trust, resource), path(resource, capability), 'inherit')
}
async function removeAvailability(resource = '') {
  if (!await confirm('Die Verfügbarkeit wieder von der Vorgabe übernehmen?', 'Override entfernen?', { okLabel: 'Entfernen' })) return
  if (resource) delete permissions.value.resourceAvailability[resource]
  else permissions.value.available = null
}
async function removeAll() {
  if (!await confirm('Alle Overrides dieses Profils für diese Integration löschen, einschließlich Ressourcen und öffentlicher Werte?', 'Overrides löschen?', { okLabel: 'Löschen' })) return
  permissions.value = { available: null, resourceAvailability: {}, privatePermissions: { policies: {}, resourcePolicies: {} }, publicPermissions: { policies: {}, resourcePolicies: {} } }
}
</script>
