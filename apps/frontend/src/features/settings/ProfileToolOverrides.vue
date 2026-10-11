<template>
  <div class="row items-center justify-end q-gutter-sm q-mb-md">
    <slot name="actions" />
    <q-btn flat no-caps color="primary" icon="add" label="Override hinzufügen" :disable="disabled || (!availableActions.length && (!shellGroup || settings.shellCustom))" @click="edit()" />
  </div>
  <q-card v-if="!overrides.length && !settings.shellCustom" flat class="app-surface q-mb-md">
    <q-card-section class="app-muted">Keine eigenen Overrides. Es gelten die geerbten Vorgaben.</q-card-section>
  </q-card>
  <PermissionFields v-model="permissions" :groups-by-trust="groupsByTrust" :model-trust-enabled="modelTrustEnabled" overrides-only readonly :disabled="disabled" :setting-group-ids="settings.shellCustom && shellGroup ? [shellGroup.id] : []">
    <template #action-controls="{ action }">
      <div class="col-12 col-md-auto row justify-end no-wrap">
        <q-btn flat round dense icon="edit" :aria-label="`${action.label} bearbeiten`" :disable="disabled" @click="edit(action.capability)" />
        <q-btn flat round dense color="negative" icon="delete_outline" :aria-label="`${action.label} entfernen`" :disable="disabled" @click="remove(action.capability)" />
      </div>
    </template>
    <template #group-settings="{ group }">
      <template v-if="settings.shellCustom && group.id === shellGroup?.id">
        <q-separator />
        <q-card-section class="row items-center q-col-gutter-md">
          <div class="col">
            <div class="text-weight-medium">Shell-Präfixe</div>
            <div class="text-caption app-muted">Eigene Werte</div>
          </div>
          <div class="col-auto row no-wrap">
            <q-btn flat round dense icon="edit" aria-label="Shell-Präfixe bearbeiten" :disable="disabled" @click="edit(null, 'setting')" />
            <q-btn flat round dense color="negative" icon="delete_outline" aria-label="Shell-Präfixe entfernen" :disable="disabled" @click="removeShellPrefixes" />
          </div>
        </q-card-section>
      </template>
    </template>
  </PermissionFields>

  <q-dialog v-model="dialogOpen" :maximized="$q.screen.lt.sm" aria-labelledby="permission-override-title">
    <q-card class="app-surface column no-wrap full-width">
      <q-card-section class="row items-center">
        <div id="permission-override-title" class="text-h6 col">{{ editing ? 'Override bearbeiten' : 'Override hinzufügen' }}</div>
        <q-btn v-close-popup flat round dense icon="close" aria-label="Dialog schließen" />
      </q-card-section>
      <q-card-section class="col scroll q-gutter-md">
        <AppSelect v-model="kind" outlined emit-value map-options label="Override-Art" :options="[{ label: 'Berechtigung', value: 'permission', disable: !editing && !availableActions.length }, { label: 'Einstellung', value: 'setting', disable: !shellGroup || (!editing && settings.shellCustom) }]" :disable="editing || disabled" />
        <template v-if="kind === 'setting'">
          <div class="text-subtitle2">Shell-Präfixe</div>
          <ShellPrefixFields v-model="shellDraft" :allow-inheritance="editing" :disabled="disabled" />
        </template>
        <AppSelect v-else v-model="draft.capability" outlined emit-value map-options use-input :input-debounce="0" label="Aktion" :options="actionOptions" :disable="editing || disabled" @filter="filterActions" />
        <template v-if="kind === 'permission' && draft.capability">
          <AppSelect v-for="audience in audiences" :key="audience.value"
            v-model="draft[audience.value]" outlined emit-value map-options :label="audience.label"
            :options="policyOptions(audience.value)" :disable="disabled"
            :hint="`Geerbte Vorgabe: ${labels[catalog[audience.value][draft.capability]?.policy] || 'Nicht verfügbar'}`" persistent-hint
          />
        </template>
        <div class="text-caption app-muted">Mit „Übernehmen“ wird das Formular aktualisiert. Anschließend die Einstellungen auf der Seite speichern.</div>
      </q-card-section>
      <q-card-actions align="right">
        <q-btn v-close-popup flat no-caps label="Abbrechen" />
        <q-btn unelevated no-caps color="primary" label="Übernehmen" :disable="disabled || (kind === 'permission' && !draft.capability)" @click="apply" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useQuasar } from 'quasar'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import PermissionFields from './PermissionFields.vue'
import ShellPrefixFields from './ShellPrefixFields.vue'
import { useConfirm } from 'src/composables/confirm'
import { policyOverride, setPolicyOverride } from 'src/utils/agent-permissions'

const permissions = defineModel({ type: Object, required: true })
const settings = defineModel('settings', { type: Object, required: true })
const props = defineProps({ groupsByTrust: { type: Object, required: true }, modelTrustEnabled: { type: Boolean, default: true }, disabled: Boolean })
const $q = useQuasar()
const { confirm } = useConfirm()
const labels = { auto: 'Automatisch', ask: 'Nachfragen', disabled: 'Deaktiviert' }
const audiences = computed(() => props.modelTrustEnabled
  ? [{ value: 'private', label: 'Private Modelle' }, { value: 'public', label: 'Öffentliche Modelle' }]
  : [{ value: 'private', label: 'Alle Modelle' }])
const branch = (trust) => permissions.value[`${trust}Permissions`]
const catalog = computed(() => Object.fromEntries(['private', 'public'].map((trust) => {
  const actions = {}
  for (const group of props.groupsByTrust[trust] || []) {
    for (const action of [
      ...(group.actions || []),
      ...(group.tools || []).flatMap((tool) => (tool.actions || []).map((action) => ({ ...action, label: `${tool.label}: ${action.label}` })))
    ]) {
      if (action.configurable === false || actions[action.capability]) continue
      actions[action.capability] = action
    }
  }
  return [trust, actions]
})))
const actionLabel = (capability) => catalog.value.private[capability]?.label || capability
const overrides = computed(() => [...new Set(audiences.value.flatMap(({ value }) => Object.keys(branch(value).policies)))])
const availableActions = computed(() => Object.values(catalog.value.private).filter((action) => !overrides.value.includes(action.capability)))
const shellGroup = computed(() => (props.groupsByTrust.private || []).find((group) =>
  (group.tools || []).some((tool) => tool.name === 'shell_run') || (group.actions || []).some((action) => action.capability === 'shell.read')))
const dialogOpen = ref(false)
const editing = ref(false)
const kind = ref('permission')
const shellDraft = ref({ shellCustom: true, shellRead: '', shellWrite: '' })
const search = ref('')
const draft = ref({ capability: null, private: 'inherit', public: 'inherit' })
const actionOptions = computed(() => (editing.value ? Object.values(catalog.value.private) : availableActions.value)
  .filter((action) => `${action.label} ${action.capability}`.toLocaleLowerCase().includes(search.value.toLocaleLowerCase()))
  .map((action) => ({ label: action.label, value: action.capability, caption: action.capability })))

function filterActions(value, update) {
  update(() => { search.value = value })
}

function policyOptions(trust) {
  return [
    { label: 'Vorgabe übernehmen', value: 'inherit' },
    ...(catalog.value[trust][draft.value.capability]?.allowedPolicies || ['disabled', 'ask', 'auto']).map((value) => ({ label: labels[value], value }))
  ]
}

function edit(capability = null, type = 'permission') {
  search.value = ''
  editing.value = Boolean(capability) || type === 'setting'
  kind.value = !editing.value && !availableActions.value.length ? 'setting' : type
  shellDraft.value = { shellCustom: true, shellRead: settings.value.shellRead, shellWrite: settings.value.shellWrite }
  draft.value = { capability, ...Object.fromEntries(audiences.value.map(({ value }) => [value, policyOverride(branch(value).policies, [capability])])) }
  dialogOpen.value = true
}

function apply() {
  if (kind.value === 'setting') Object.assign(settings.value, shellDraft.value)
  else for (const { value } of audiences.value) setPolicyOverride(branch(value).policies, [draft.value.capability], draft.value[value])
  dialogOpen.value = false
}

async function removeShellPrefixes() {
  if (!await confirm('Eigene Shell-Präfixe entfernen und die Vorgabe wieder übernehmen?', 'Override entfernen?', { okLabel: 'Entfernen' })) return
  settings.value.shellCustom = false
}

async function remove(capability) {
  if (!await confirm(`Override „${actionLabel(capability)}“ entfernen? Für die angezeigten Modellbereiche gelten danach wieder die geerbten Vorgaben.`, 'Override entfernen?', { okLabel: 'Entfernen' })) return
  for (const { value } of audiences.value) setPolicyOverride(branch(value).policies, [capability], 'inherit')
}
</script>
