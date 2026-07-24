import { normalizeModelCapabilities } from 'src/utils/provider-models'

export const DEFAULT_MODEL_RUNTIME = Object.freeze({
  capabilities: Object.freeze(['chat']),
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
  reasoningSummary: false
})

function modelValue(model, camelKey, snakeKey) {
  if (model?.[camelKey] !== undefined) return model[camelKey]
  if (model?.config?.[camelKey] !== undefined) return model.config[camelKey]
  if (model?.config?.[snakeKey] !== undefined) return model.config[snakeKey]
  return DEFAULT_MODEL_RUNTIME[camelKey]
}

export function modelRuntimeForm(model) {
  return {
    capabilities: normalizeModelCapabilities(model?.capabilities),
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
    supportsImageInput: modelValue(model, 'supportsImageInput', 'supports_image_input'),
    trustLevel: modelValue(model, 'trustLevel', 'trust_level'),
    pseudonymizationEnabled: modelValue(model, 'pseudonymizationEnabled', 'pseudonymization_enabled'),
    memoryPolicy: modelValue(model, 'memoryPolicy', 'memory_policy'),
    webfetchPolicy: modelValue(model, 'webfetchPolicy', 'webfetch_policy'),
    effectiveWebfetchPolicy: modelValue(model, 'effectiveWebfetchPolicy', 'effective_webfetch_policy'),
    reasoningSummary: modelValue(model, 'reasoningSummary', 'reasoning_summary')
  }
}

export function modelRuntimePayload(form) {
  return {
    capabilities: normalizeModelCapabilities(form.capabilities),
    context_window_tokens: Number(form.contextWindowTokens),
    use_discovered_context_window: Boolean(form.useDiscoveredContextWindow),
    max_output_tokens: Number(form.maxOutputTokens),
    characters_per_token: Number(form.charactersPerToken),
    supports_tools: Boolean(form.supportsTools),
    supports_parallel_tool_calls: Boolean(form.supportsTools && form.supportsParallelToolCalls),
    supports_streaming: Boolean(form.supportsStreaming),
    supports_image_input: Boolean(form.supportsImageInput),
    trust_level: form.trustLevel || null,
    pseudonymization_enabled: form.pseudonymizationEnabled ?? null,
    memory_policy: form.memoryPolicy || 'inherit',
    webfetch_policy: form.webfetchPolicy || 'inherit',
    reasoning_summary: Boolean(form.reasoningSummary)
  }
}
