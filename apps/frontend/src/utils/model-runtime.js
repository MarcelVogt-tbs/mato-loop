import { normalizeModelCapabilities } from 'src/utils/provider-models'

export const REASONING_EFFORT_OPTIONS = Object.freeze([
  { label: 'Providerstandard', value: null },
  { label: 'Aus', value: 'none' },
  { label: 'Minimal', value: 'minimal' },
  { label: 'Niedrig', value: 'low' },
  { label: 'Mittel', value: 'medium' },
  { label: 'Hoch', value: 'high' },
  { label: 'Sehr hoch', value: 'xhigh' }
])

export function reasoningEffortLabel(value) {
  return REASONING_EFFORT_OPTIONS.find((option) => option.value === value)?.label || 'Providerstandard'
}

export const DEFAULT_MODEL_RUNTIME = Object.freeze({
  capabilities: Object.freeze(['chat']),
  inputModalities: Object.freeze(['text']),
  contextWindowTokens: 128000,
  discoveredContextWindowTokens: null,
  useDiscoveredContextWindow: false,
  contextWindowSource: 'override',
  maxOutputTokens: 16384,
  charactersPerToken: 4,
  supportsTools: true,
  supportsParallelToolCalls: true,
  supportsStreaming: true,
  supportsImageInput: false,
  trustLevel: null,
  pseudonymizationEnabled: null,
  memoryPolicy: 'inherit',
  webfetchPolicy: 'inherit',
  effectiveWebfetchPolicy: null,
  reasoningSummary: false,
  reasoningEffort: null
})

function modelValue(model, camelKey, snakeKey) {
  if (model?.[camelKey] !== undefined) return model[camelKey]
  if (model?.config?.[camelKey] !== undefined) return model.config[camelKey]
  if (model?.config?.[snakeKey] !== undefined) return model.config[snakeKey]
  return DEFAULT_MODEL_RUNTIME[camelKey]
}

const INPUT_MODALITIES = new Set(['text', 'image', 'audio', 'video'])

function modelInputModalities(model) {
  const value = model?.inputModalities
    ?? model?.config?.inputModalities
    ?? model?.config?.input_modalities
  if (Array.isArray(value)) {
    return [...new Set(value.filter((modality) => INPUT_MODALITIES.has(modality)))]
  }
  return modelValue(model, 'supportsImageInput', 'supports_image_input')
    ? ['text', 'image']
    : ['text']
}

export function modelRuntimeForm(model) {
  const inputModalities = modelInputModalities(model)
  return {
    capabilities: normalizeModelCapabilities(model?.capabilities),
    inputModalities,
    contextWindowTokens: modelValue(model, 'contextWindowTokens', 'context_window_tokens'),
    discoveredContextWindowTokens: modelValue(
      model,
      'discoveredContextWindowTokens',
      'discovered_context_window_tokens'
    ),
    useDiscoveredContextWindow: modelValue(
      model,
      'useDiscoveredContextWindow',
      'use_discovered_context_window'
    ),
    contextWindowSource: modelValue(model, 'contextWindowSource', 'context_window_source'),
    maxOutputTokens: modelValue(model, 'maxOutputTokens', 'max_output_tokens'),
    charactersPerToken: modelValue(model, 'charactersPerToken', 'characters_per_token'),
    supportsTools: modelValue(model, 'supportsTools', 'supports_tools'),
    supportsParallelToolCalls: modelValue(model, 'supportsParallelToolCalls', 'supports_parallel_tool_calls'),
    supportsStreaming: modelValue(model, 'supportsStreaming', 'supports_streaming'),
    supportsImageInput: inputModalities.includes('image'),
    trustLevel: modelValue(model, 'trustLevel', 'trust_level'),
    pseudonymizationEnabled: modelValue(model, 'pseudonymizationEnabled', 'pseudonymization_enabled'),
    memoryPolicy: modelValue(model, 'memoryPolicy', 'memory_policy'),
    webfetchPolicy: modelValue(model, 'webfetchPolicy', 'webfetch_policy'),
    effectiveWebfetchPolicy: modelValue(model, 'effectiveWebfetchPolicy', 'effective_webfetch_policy'),
    reasoningSummary: modelValue(model, 'reasoningSummary', 'reasoning_summary'),
    reasoningEffort: modelValue(model, 'reasoningEffort', 'reasoning_effort')
  }
}

export function modelRuntimePayload(form) {
  const supportsImageInput = Array.isArray(form.inputModalities)
    ? form.inputModalities.includes('image')
    : Boolean(form.supportsImageInput)
  return {
    capabilities: normalizeModelCapabilities(form.capabilities),
    input_modalities: Array.isArray(form.inputModalities)
      ? [...new Set(form.inputModalities.filter((modality) => INPUT_MODALITIES.has(modality)))]
      : supportsImageInput ? ['text', 'image'] : ['text'],
    context_window_tokens: Number(form.contextWindowTokens),
    use_discovered_context_window: Boolean(form.useDiscoveredContextWindow),
    max_output_tokens: Number(form.maxOutputTokens),
    characters_per_token: Number(form.charactersPerToken),
    supports_tools: Boolean(form.supportsTools),
    supports_parallel_tool_calls: Boolean(form.supportsTools && form.supportsParallelToolCalls),
    supports_streaming: Boolean(form.supportsStreaming),
    supports_image_input: supportsImageInput,
    trust_level: form.trustLevel || null,
    pseudonymization_enabled: form.pseudonymizationEnabled ?? null,
    memory_policy: form.memoryPolicy || 'inherit',
    webfetch_policy: form.webfetchPolicy || 'inherit',
    reasoning_summary: Boolean(form.reasoningSummary),
    reasoning_effort: form.reasoningEffort || null
  }
}
