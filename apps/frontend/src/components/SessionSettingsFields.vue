<template>
  <div :class="compact ? 'row items-center q-gutter-xs' : 'column q-gutter-md'">
    <q-banner v-if="showWarning && warning" dense rounded class="app-surface-tonal">
      <template #avatar><q-icon name="warning_amber" color="warning" /></template>
      {{ warning }}
    </q-banner>

    <section v-if="grouped && hasSessionFields" aria-labelledby="session-settings-title">
      <div id="session-settings-title" class="text-subtitle1 text-weight-bold q-mb-md">
        Session-Einstellungen
      </div>
      <div class="column q-gutter-md">
        <AgentField v-if="has('agent')" />
        <ComputerField v-if="has('computer')" />
        <MemoryField v-if="has('memory')" />
        <WebSearchField v-if="has('webSearch')" />
      </div>
    </section>

    <section v-if="grouped && hasLlmFields" aria-labelledby="session-llm-settings-title">
      <div id="session-llm-settings-title" class="text-subtitle1 text-weight-bold q-mb-md">
        LLM-Einstellungen
      </div>
      <div class="column q-gutter-md">
        <ModelField v-if="has('model')" />
        <ReasoningField v-if="has('reasoning')" />
      </div>
    </section>

    <template v-if="!grouped">
      <AgentField v-if="has('agent')" />
      <ModelField v-if="has('model')" />
      <ReasoningField v-if="has('reasoning') && (!compact || reasoningEffortOptions.length)" />
      <ComputerField v-if="has('computer')" />
      <MemoryField v-if="has('memory')" />
      <WebSearchField v-if="has('webSearch')" />
    </template>
  </div>
</template>

<script setup>
import { computed, defineComponent, h } from 'vue'
import { QBanner, QIcon, QItem, QItemLabel, QItemSection, QSelect, QTooltip, useQuasar } from 'quasar'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'
import { useProviderStore } from 'stores/providers'
import { apiErrorMessage } from 'src/utils/api-errors'
import { modelReasoningEffortOptions } from 'src/utils/model-runtime'

defineOptions({ name: 'SessionSettingsFields' })

const SESSION_FIELDS = ['agent', 'computer', 'memory', 'webSearch']
const LLM_FIELDS = ['model', 'reasoning']
const props = defineProps({
  session: { type: Object, default: null },
  disabled: Boolean,
  compact: Boolean,
  grouped: Boolean,
  showWarning: Boolean,
  fields: { type: Array, default: () => ['agent', 'model', 'reasoning', 'computer', 'memory', 'webSearch'] }
})
const emit = defineEmits(['update'])
const $q = useQuasar()
const agentProfiles = useAgentProfileStore()
const executionAgents = useExecutionAgentStore()
const integrations = useIntegrationStore()
const providers = useProviderStore()

const has = (field) => props.fields.includes(field)
const hasSessionFields = computed(() => SESSION_FIELDS.some(has))
const hasLlmFields = computed(() => LLM_FIELDS.some(has))
const selectedAgent = computed(() => agentProfiles.byId(props.session?.agentProfileId))
const agentProfileOptions = computed(() => {
  const options = agentProfiles.agents.map((agent) => ({
    label: agent.name,
    value: agent.id,
    icon: 'smart_toy',
    caption: agent.description
  }))
  if (props.session?.agentProfileId && !selectedAgent.value) {
    options.unshift({
      label: 'Nicht verfügbares Agent-Profil',
      value: props.session.agentProfileId,
      icon: 'warning_amber',
      caption: 'Nicht mehr verfügbar',
      disable: true
    })
  }
  return options
})
const selectedAgentIcon = computed(() => (
  !props.session?.agentProfileId || selectedAgent.value ? 'smart_toy' : 'warning_amber'
))
const selectedExecutionAgent = computed(() => (
  executionAgents.byId(props.session?.preferredExecutionAgentId)
))
const executionAgentOptions = computed(() => {
  const options = [
    { label: 'Kein Computer', value: null, connected: null, caption: 'Nur Backend-Funktionen verwenden' },
    ...executionAgents.items.map((agent) => ({
      label: agent.alias,
      value: agent.id,
      connected: agent.connected,
      caption: agent.pairingState === 'pending'
        ? 'Kopplung ausstehend'
        : agent.pairingState === 'unpaired'
          ? 'Nicht gekoppelt'
          : agent.connected ? agent.computerName || agent.platform : 'Offline'
    }))
  ]
  if (props.session?.preferredExecutionAgentId && !selectedExecutionAgent.value) {
    options.push({
      label: 'Nicht verfügbarer Computer',
      value: props.session.preferredExecutionAgentId,
      connected: false,
      caption: 'Nicht mehr eingerichtet'
    })
  }
  return options
})
const liveWorkspaceContext = computed(() => executionAgents.workspaceContextFor(props.session))
const executionAgentMessage = computed(() => {
  if (executionAgents.loadFailed) return 'Der Status der Computer konnte nicht geladen werden.'
  if (!props.session?.preferredExecutionAgentId) return 'Ohne Computer stehen keine lokalen Datei- und Shell-Funktionen zur Verfügung.'
  if (!selectedExecutionAgent.value) return 'Der ausgewählte Computer ist nicht mehr eingerichtet.'
  if (selectedExecutionAgent.value.pairingState === 'pending') return 'Die Kopplung des ausgewählten Computers steht noch aus.'
  if (selectedExecutionAgent.value.pairingState === 'unpaired') return 'Der ausgewählte Computer ist nicht gekoppelt.'
  if (!selectedExecutionAgent.value.connected) return 'Der ausgewählte Computer ist offline. Es erfolgt kein automatischer Wechsel.'
  if (!selectedExecutionAgent.value.operations?.includes('filesystem.list')) return 'Der ausgewählte Computer bietet keinen Dateizugriff an.'
  if (!liveWorkspaceContext.value.connected) return 'Der ausgewählte Computer ist nicht verfügbar.'
  return ''
})
const effectiveModelId = computed(() => props.session?.modelId || providers.defaultModelId)
const modelSelectionOptions = computed(() => providers.selectionOptions(effectiveModelId.value, true))
const selectedModelConfig = computed(() => providers.models.find((model) => model.id === effectiveModelId.value))
const selectedModelTrustIcon = computed(() => selectedModelConfig.value?.effectiveTrustLevel === 'private' ? 'lock' : 'public')
const reasoningEffortOptions = computed(() => modelReasoningEffortOptions(selectedModelConfig.value))
const modelSupportsTools = computed(() => selectedModelConfig.value?.supportsTools !== false)
const memoryModeOptions = [
  { label: 'Nutzen und speichern', value: 'full', icon: 'memory', description: 'Vorherige Chats und Erinnerungen nutzen; neue Erinnerungen speichern' },
  { label: 'Nur nutzen', value: 'read_only', icon: 'visibility', description: 'Vorherige Chats und Erinnerungen nutzen; nichts speichern' },
  { label: 'Sitzungsübergreifend aus', value: 'disabled', icon: 'memory_alt', description: 'Keine Inhalte aus vorherigen Chats oder Erinnerungen nutzen' }
]
const effectiveMemoryMode = computed(() => props.session?.effectiveMemoryMode || props.session?.memoryMode || 'disabled')
const memoryMessage = computed(() => {
  if (props.session?.memoryUnavailableReason) {
    return apiErrorMessage({ error: { code: props.session.memoryUnavailableReason } })
  }
  return {
    full: 'Vorherige Chats und Erinnerungen nutzen; neue Erinnerungen speichern.',
    read_only: 'Vorherige Chats und Erinnerungen nutzen; keine neuen Erinnerungen speichern.',
    disabled: 'Keine Inhalte aus vorherigen Chats oder Erinnerungen nutzen.'
  }[effectiveMemoryMode.value]
})
const memoryModeIcon = computed(() => ({ full: 'memory', read_only: 'visibility', disabled: 'memory_alt' })[props.session?.memoryMode] || 'memory')
const searchAvailable = computed(() => integrations.search.effectiveEnabled)
const webSearchOptions = computed(() => [
  {
    label: searchAvailable.value && modelSupportsTools.value ? 'Erzwingen' : 'Erzwingen (nicht verfügbar)',
    value: 'required',
    icon: 'travel_explore',
    disable: !searchAvailable.value || !modelSupportsTools.value,
    description: 'Vor jeder Antwort erfolgreich im Web suchen'
  },
  {
    label: searchAvailable.value && modelSupportsTools.value ? 'Verfügbar' : 'Nicht verfügbar',
    value: 'available',
    icon: 'public',
    description: 'Das Modell entscheidet über die Websuche'
  },
  { label: 'Deaktiviert', value: 'disabled', icon: 'public_off', description: 'Kein Websuch-Tool anbieten' }
])
const webSearchIcon = computed(() => ({
  required: 'travel_explore',
  available: 'public',
  disabled: 'public_off'
})[props.session?.webSearchPolicy] || 'public')
const webSearchTooltip = computed(() => {
  if (!modelSupportsTools.value) return 'Das ausgewählte Modell unterstützt keine Tools.'
  if (!searchAvailable.value) {
    return integrations.search.unavailableReason === 'integration.disabled'
      ? 'Websuche ist deaktiviert'
      : 'Websuche ist nicht eingerichtet'
  }
  return ''
})
const webSearchMessage = computed(() => (
  webSearchTooltip.value
  || webSearchOptions.value.find((option) => option.value === props.session?.webSearchPolicy)?.description
  || 'Websuche für diese Session konfigurieren.'
))
const requiredSearchUnavailable = computed(() => (
  props.session?.webSearchPolicy === 'required' && (!searchAvailable.value || !modelSupportsTools.value)
))
const warning = computed(() => {
  const warnings = []
  if (!modelSupportsTools.value) warnings.push('Wähle ein Agentmodell mit Tool-Unterstützung.')
  if (
    props.session?.memoryMode !== 'disabled'
    && props.session?.memoryUnavailableReason !== 'session.modelUnavailable'
    && (props.session?.memoryUnavailableReason || (!props.session?.memoryToolsAvailable && effectiveMemoryMode.value !== 'disabled'))
  ) warnings.push(memoryMessage.value)
  if (requiredSearchUnavailable.value) warnings.push(webSearchTooltip.value || 'Die erforderliche Websuche ist nicht verfügbar.')
  if (executionAgentMessage.value) warnings.push(executionAgentMessage.value)
  return warnings.join(' ')
})

function update(patch) {
  if (props.session && !props.disabled) emit('update', patch)
}

function selectModel(modelId) {
  const model = providers.models.find((item) => item.id === modelId)
  const effort = props.session?.reasoningEffort
  update({
    model_id: modelId,
    ...(effort && !model?.reasoningEfforts?.includes(effort) ? { reasoning_effort: null } : {})
  })
}

function selectExecutionAgent(value) {
  const agent = executionAgents.byId(value)
  update({
    preferred_execution_agent_id: value,
    current_directory: agent?.homeDirectory || null
  })
}

const selectProps = (extra = {}) => ({
  outlined: !props.compact,
  borderless: props.compact,
  dense: props.compact || $q.screen.gt.xs,
  optionsDense: true,
  emitValue: true,
  mapOptions: true,
  popupContentClass: 'app-menu-popup',
  disable: props.disabled,
  ...extra
})
const optionSlot = (scope, icon) => h(QItem, scope.itemProps, {
  default: () => [
    h(QItemSection, { avatar: true }, () => h(QIcon, { name: icon(scope.opt) })),
    h(QItemSection, null, () => [
      h(QItemLabel, null, () => scope.opt.label),
      scope.opt.caption ? h(QItemLabel, { caption: true }, () => scope.opt.caption) : null,
      scope.opt.description ? h(QItemLabel, { caption: true }, () => scope.opt.description) : null
    ])
  ]
})

const AgentField = defineComponent(() => () => h(QSelect, selectProps({
  modelValue: props.session?.agentProfileId ?? agentProfiles.defaultAgentProfileId,
  options: agentProfileOptions.value,
  label: props.compact ? 'Agent' : 'Agent-Profil',
  class: props.compact ? 'composer-agent-mode' : undefined,
  hint: props.compact ? undefined : 'Agent-Profil für diese Session.',
  persistentHint: !props.compact,
  disable: props.disabled || agentProfiles.loading,
  'onUpdate:modelValue': (value) => update({ agent_profile_id: value })
}), {
  prepend: () => h(QIcon, { name: selectedAgentIcon.value, size: props.compact ? '18px' : undefined }),
  option: (scope) => optionSlot(scope, (option) => option.icon)
}))
const ModelField = defineComponent(() => () => h(QSelect, selectProps({
  modelValue: effectiveModelId.value,
  options: modelSelectionOptions.value,
  label: props.compact ? 'LLM' : 'Modell',
  class: props.compact ? 'composer-model' : undefined,
  hint: props.compact ? undefined : modelSupportsTools.value ? 'Modell für diese Session.' : 'Das ausgewählte Modell unterstützt keine Tools.',
  persistentHint: !props.compact,
  'onUpdate:modelValue': selectModel
}), {
  prepend: () => h(QIcon, { name: selectedModelTrustIcon.value, size: props.compact ? '18px' : undefined }),
  option: (scope) => optionSlot(scope, (option) => option.trustIcon)
}))
const ReasoningField = defineComponent(() => () => h(QSelect, selectProps({
  modelValue: props.session?.reasoningEffort ?? null,
  options: reasoningEffortOptions.value,
  label: 'Denken',
  class: props.compact ? 'composer-reasoning' : undefined,
  hint: props.compact ? undefined : reasoningEffortOptions.value.length
    ? 'Reasoning-Aufwand für diese Session.'
    : 'Das ausgewählte Modell bietet keine Reasoning-Stufen an.',
  persistentHint: !props.compact,
  disable: props.disabled || !reasoningEffortOptions.value.length,
  'onUpdate:modelValue': (value) => update({ reasoning_effort: value })
}), props.compact ? {
  default: () => h(QTooltip, null, () => 'Reasoning-Aufwand für diese Session')
} : undefined))
const ComputerField = defineComponent(() => () => [
  h(QSelect, selectProps({
    modelValue: props.session?.preferredExecutionAgentId,
    options: executionAgentOptions.value,
    label: 'Computer',
    hint: 'Der Computer wird für den nächsten Lauf verwendet.',
    persistentHint: true,
    'onUpdate:modelValue': selectExecutionAgent
  }), {
    prepend: () => h(QIcon, { name: 'computer' }),
    option: (scope) => optionSlot(scope, (option) => option.connected === false ? 'cloud_off' : 'computer')
  }),
  executionAgentMessage.value && !props.showWarning
    ? h(QBanner, { dense: true, rounded: true, class: 'app-surface-tonal' }, {
        avatar: () => h(QIcon, {
          name: selectedExecutionAgent.value?.connected === false ? 'cloud_off' : 'info_outline',
          color: 'warning'
        }),
        default: () => executionAgentMessage.value
      })
    : null
])
const MemoryField = defineComponent(() => () => h(QSelect, selectProps({
  modelValue: props.session?.memoryMode,
  options: memoryModeOptions,
  label: 'Memory',
  hint: memoryMessage.value,
  persistentHint: true,
  'onUpdate:modelValue': (value) => update({ memory_mode: value })
}), {
  prepend: () => h(QIcon, { name: memoryModeIcon.value }),
  option: (scope) => optionSlot(scope, (option) => option.icon)
}))
const WebSearchField = defineComponent(() => () => h(QSelect, selectProps({
  modelValue: props.session?.webSearchPolicy,
  options: webSearchOptions.value,
  label: 'Websuche',
  hint: webSearchMessage.value,
  persistentHint: true,
  'onUpdate:modelValue': (value) => update({ web_search_policy: value })
}), {
  prepend: () => h(QIcon, { name: webSearchIcon.value }),
  option: (scope) => optionSlot(scope, (option) => option.icon)
}))

defineExpose({ warning })
</script>
