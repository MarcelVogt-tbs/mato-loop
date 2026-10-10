import { expect, it } from 'vitest'
import { sessionModel } from '../src/features/chat/session-model'

it('resolves each agent override above session and effective defaults', () => {
  const defaults = { defaultModelId: 'global', reasoningEffort: 'low' }
  const session = { modelId: 'session', sessionProfile: { agentModels: { plan: { modelId: 'plan-model', reasoningEffort: 'high' } } } }
  const options = ['plan-model', 'session', 'global'].map((value) => ({ value }))
  expect([sessionModel(session, defaults, options, 'plan'), sessionModel(session, defaults, options, 'build'), sessionModel({}, defaults, options), sessionModel(session, defaults, options.slice(2), 'plan')]).toEqual([
    { modelId: 'plan-model', reasoningEffort: 'high' }, { modelId: 'session', reasoningEffort: 'low' }, { modelId: 'global', reasoningEffort: 'low' }, { modelId: 'global', reasoningEffort: 'high' }
  ])
})
