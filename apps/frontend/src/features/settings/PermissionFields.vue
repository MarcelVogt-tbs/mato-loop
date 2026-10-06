<template>
  <q-card v-for="group in groups" :key="group.id" flat class="app-surface q-mb-md">
    <q-card-section class="row items-center q-gutter-md">
      <q-icon :name="group.icon || 'security'" color="primary" size="sm" />
      <div class="text-subtitle1 text-weight-bold">{{ group.label }}</div>
    </q-card-section>
    <template v-for="action in group.actions" :key="action.capability">
      <q-separator />
      <q-card-section class="row items-start q-col-gutter-md">
        <div class="col-12 col-md">
          <div class="text-weight-medium">{{ action.label }}</div>
          <div class="text-caption app-muted">{{ action.capability }}</div>
          <div v-if="action.available === false" class="text-caption text-warning">Technisch nicht verfügbar</div>
        </div>
        <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
          <AppSelect
            :model-value="policyOverride(branch(audience.value).policies, [action.capability])"
            outlined dense emit-value map-options :label="audience.label" :aria-label="`${action.label}: ${audience.label}`"
            :options="options(action)" :disable="disabled || action.available === false"
            :hint="`Wirksam: ${label(effective(audience.value, action))}`" persistent-hint
            @update:model-value="setPolicyOverride(branch(audience.value).policies, [action.capability], $event)"
          />
        </div>
      </q-card-section>
    </template>
  </q-card>
  <q-expansion-item v-for="resource in resources" :key="resource.key" :label="resource.label" icon="folder_open" class="app-surface q-mb-md">
    <q-card-section v-for="action in resourceActions(resource)" :key="action.capability" class="row q-col-gutter-md">
      <div class="col-12 col-md text-weight-medium">{{ action.label }}</div>
      <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
        <AppSelect
          :model-value="policyOverride(branch(audience.value).resourcePolicies, [resource.key, action.capability])"
          outlined dense emit-value map-options :label="audience.label" :aria-label="`${resource.label}: ${action.label}, ${audience.label}`"
          :options="options(action)"
          :disable="disabled || resource.hardUnavailableCapabilities?.includes(action.capability)"
          :hint="`Wirksam: ${label(effective(audience.value, action, resource))}`" persistent-hint
          @update:model-value="setPolicyOverride(branch(audience.value).resourcePolicies, [resource.key, action.capability], $event)"
        />
      </div>
    </q-card-section>
  </q-expansion-item>
</template>

<script setup>
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { computed } from 'vue'
import { policyOverride, setPolicyOverride } from 'src/utils/agent-permissions'

const permissions = defineModel({ type: Object, required: true })
const props = defineProps({ groupsByTrust: { type: Object, required: true }, resources: { type: Array, default: () => [] }, inheritedResources: { type: Object, default: () => ({}) }, disabled: Boolean })
const audiences = [{ value: 'private', label: 'Private Modelle' }, { value: 'public', label: 'Öffentliche Modelle' }]
const labels = { disabled: 'Deaktiviert', ask: 'Nachfragen', auto: 'Automatisch' }
const label = (value) => labels[value] || value
const branch = (trust) => permissions.value[`${trust}Permissions`]
const groups = computed(() => {
  const seen = new Set()
  return (props.groupsByTrust.private || []).map((group) => ({ ...group, actions: [
    ...(group.actions || []), ...(group.tools || []).flatMap((tool) => (tool.actions || []).map((action) => ({ ...action, label: `${tool.label}: ${action.label}`, available: tool.available !== false && action.available !== false })))
  ].filter((action) => {
    if (action.configurable === false || seen.has(action.capability)) return false
    seen.add(action.capability)
    return true
  }) })).filter((group) => group.actions.length)
})
const actions = (trust) => (props.groupsByTrust[trust] || []).flatMap((group) => [...(group.actions || []), ...(group.tools || []).flatMap((tool) => tool.actions || [])])
const resourceActions = (resource) => groups.value.flatMap((group) => group.actions).filter((action) => !resource.applicableCapabilities || resource.applicableCapabilities.includes(action.capability))
function inherited(trust, action, resource) {
  if (resource) return props.inheritedResources[trust]?.find((item) => item.key === resource.key)?.effectivePolicies?.[action.capability] || effective(trust, action)
  const original = actions(trust).find((item) => item.capability === action.capability)
  return original?.policy || original?.effectiveIntegrationPolicy || 'ask'
}
function effective(trust, action, resource) {
  if (action.available === false || resource?.hardUnavailableCapabilities?.includes(action.capability)) return 'disabled'
  const value = resource ? branch(trust).resourcePolicies?.[resource.key]?.[action.capability] : branch(trust).policies[action.capability]
  const result = value || inherited(trust, action, resource)
  return result === 'auto' && action.allowedPolicies && !action.allowedPolicies.includes('auto') ? 'ask' : result
}
function options(action) {
  return [{ label: 'Vorgabe', value: 'inherit' }, ...(action.allowedPolicies || ['disabled', 'ask', 'auto']).map((value) => ({ label: label(value), value }))]
}
</script>
