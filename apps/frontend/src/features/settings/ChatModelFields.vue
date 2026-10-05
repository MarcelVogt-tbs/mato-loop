<template>
  <div>
    <div class="row q-col-gutter-md">
      <q-select :model-value="modelValue.modelId" :display-value="modelValue.modelId ? undefined : inheritedModelLabel" class="col-12 col-sm-6" outlined dense clearable emit-value map-options label="Agentmodell" :options="providers.selectionOptions(modelValue.modelId || inherited.modelId, true)" :disable="disabled" :hint="inheritedModelLabel" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" @update:model-value="selectModel" />
      <q-select :model-value="modelValue.reasoningEffort" class="col-12 col-sm-6" outlined dense emit-value map-options label="Reasoning-Stufe" :options="reasoningOptions" :disable="disabled || reasoningOptions.length < 2" @update:model-value="$emit('update:modelValue', { ...modelValue, reasoningEffort: $event })" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useProviderStore } from 'stores/providers'
import { modelReasoningEffortOptions, reasoningEffortLabel } from 'src/utils/model-runtime'

const props = defineProps({ modelValue: { type: Object, required: true }, inherited: { type: Object, default: () => ({}) }, automatic: Boolean, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const providers = useProviderStore()
const selected = computed(() => (providers.availableModels || providers.models).find((model) => model.id === (props.modelValue.modelId || props.inherited.modelId)))
const reasoningOptions = computed(() => [{ label: `Vorgabe (${props.inherited.reasoningEffort ? reasoningEffortLabel(props.inherited.reasoningEffort) : 'Modellstandard'})`, value: null }, ...modelReasoningEffortOptions(selected.value).filter((item) => item.value !== null)])
const modelLabel = (id) => providers.selectionOptions(id, true).find((option) => option.value === id)?.label || 'Nicht festgelegt'
const inheritedModelLabel = computed(() => `${props.automatic ? 'Automatisch' : 'Vorgabe'}: ${modelLabel(props.inherited.modelId)}`)
function selectModel(modelId) {
  const model = (providers.availableModels || providers.models).find((item) => item.id === (modelId || props.inherited.modelId))
  emit('update:modelValue', { modelId, reasoningEffort: model?.reasoningEfforts?.includes(props.modelValue.reasoningEffort) ? props.modelValue.reasoningEffort : null })
}
</script>
