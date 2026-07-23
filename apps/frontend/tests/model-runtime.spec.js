import { describe, expect, it } from 'vitest'

import { DEFAULT_MODEL_RUNTIME, modelRuntimeForm, modelRuntimePayload } from '../src/utils/model-runtime'

describe('model runtime configuration', () => {
  it('uses safe defaults for a new model', () => {
    expect(modelRuntimeForm()).toEqual(DEFAULT_MODEL_RUNTIME)
  })

  it('reads camelCase API fields and preserves false capabilities', () => {
    expect(modelRuntimeForm({
      contextWindowTokens: 64000,
      maxOutputTokens: 8000,
      charactersPerToken: 3.5,
      supportsTools: false,
      supportsParallelToolCalls: false,
      supportsStreaming: false,
      supportsImageInput: true,
      trustLevel: null,
      pseudonymizationEnabled: false,
      memoryPolicy: 'deny',
      webfetchPolicy: 'auto_public',
      effectiveWebfetchPolicy: 'disabled',
      reasoningSummary: false
    })).toEqual({
      contextWindowTokens: 64000,
      maxOutputTokens: 8000,
      charactersPerToken: 3.5,
      supportsTools: false,
      supportsParallelToolCalls: false,
      supportsStreaming: false,
      supportsImageInput: true,
      trustLevel: null,
      pseudonymizationEnabled: false,
      memoryPolicy: 'deny',
      webfetchPolicy: 'auto_public',
      effectiveWebfetchPolicy: 'disabled',
      reasoningSummary: false
    })
  })

  it('maps form values to additive provider request fields', () => {
    expect(modelRuntimePayload({
      contextWindowTokens: '32000',
      maxOutputTokens: '4096',
      charactersPerToken: '4.2',
      supportsTools: false,
      supportsParallelToolCalls: true,
      supportsStreaming: true,
      supportsImageInput: true,
      trustLevel: 'private',
      pseudonymizationEnabled: null,
      memoryPolicy: 'allow',
      webfetchPolicy: 'auto_allowed',
      reasoningSummary: false
    })).toEqual({
      context_window_tokens: 32000,
      max_output_tokens: 4096,
      characters_per_token: 4.2,
      supports_tools: false,
      supports_parallel_tool_calls: false,
      supports_streaming: true,
      supports_image_input: true,
      trust_level: 'private',
      pseudonymization_enabled: null,
      memory_policy: 'allow',
      webfetch_policy: 'auto_allowed',
      reasoning_summary: false
    })
  })

  it('preserves inherited model policy values', () => {
    expect(modelRuntimeForm({
      trustLevel: null,
      pseudonymizationEnabled: null
    })).toMatchObject({
      trustLevel: null,
      pseudonymizationEnabled: null
    })
  })
})
