<template>
  <div>
    <div class="row q-col-gutter-md">
      <AppSelect :model-value="modelValue.modelId ?? null" class="col-12 col-sm-6" outlined dense emit-value map-options label="Agentmodell" :options="modelOptions" :disable="disabled" :hint="modelValue.modelId ? undefined : `Effektiv: ${modelOptions.find((option) => option.value && option.value === selected?.id)?.label || 'Auswahl zur Laufzeit'}`" @update:model-value="selectModel" />
      <AppSelect :model-value="modelValue.reasoningEffort ?? null" class="col-12 col-sm-6" outlined dense emit-value map-options label="Reasoning-Stufe" :options="reasoningOptions" :disable="disabled || reasoningOptions.length < 2 && !modelValue.reasoningEffort" :hint="modelValue.reasoningEffort ? undefined : `Effektiv: ${reasoningLabel}`" @update:model-value="$emit('update:modelValue', { ...modelValue, reasoningEffort: $event })" />
    </div>
  </div>
</template>

<script setup>
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { computed } from 'vue'
import { useProviderStore } from 'stores/providers'
import { modelReasoningEffortOptions, reasoningEffortLabel } from 'src/utils/model-runtime'

const props = defineProps({ modelValue: { type: Object, required: true }, inherited: { type: Object, default: () => ({}) }, disabled: Boolean })
const emit = defineEmits(['update:modelValue'])
const providers = useProviderStore()
const selected = computed(() => (providers.availableModels || providers.models).find((model) => model.id === (props.modelValue.modelId || props.inherited.modelId)))
const modelOptions = computed(() => [{ label: 'Automatische Auswahl', value: null }, ...providers.selectionOptions(props.modelValue.modelId || props.inherited.modelId, true)])
const reasoningOptions = computed(() => [{ label: 'Automatische Auswahl', value: null }, ...modelReasoningEffortOptions(selected.value).filter((item) => item.value !== null)])
const reasoningLabel = computed(() => {
  const inherited = selected.value?.reasoningEfforts?.includes(props.inherited.reasoningEffort) ? props.inherited.reasoningEffort : null
  return reasoningEffortLabel(props.modelValue.reasoningEffort || inherited || selected.value?.reasoningEffort)
})
function selectModel(modelId) {
  const model = (providers.availableModels || providers.models).find((item) => item.id === (modelId || props.inherited.modelId))
  emit('update:modelValue', { modelId, reasoningEffort: model?.reasoningEfforts?.includes(props.modelValue.reasoningEffort) ? props.modelValue.reasoningEffort : null })
}
</script>
