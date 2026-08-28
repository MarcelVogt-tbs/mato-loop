import { describe, expect, it } from 'vitest'

import { policyOverride, setPolicyOverride } from '../src/utils/agent-permissions'

describe('agent permission overrides', () => {
  it('stores only explicit policies and prunes empty hierarchy entries on inherit', () => {
    const policies = {}

    setPolicyOverride(policies, ['calendar-1', 'calendar:work', 'calendar.read'], 'auto')
    expect(policyOverride(policies, ['calendar-1', 'calendar:work', 'calendar.read'])).toBe('auto')

    setPolicyOverride(policies, ['calendar-1', 'calendar:work', 'calendar.read'], 'inherit')
    expect(policies).toEqual({})
    expect(policyOverride(policies, ['calendar-1', 'calendar:work', 'calendar.read'])).toBe('inherit')
  })
})
