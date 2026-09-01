import { describe, expect, it, vi } from 'vitest'
import { randomUuid } from '../src/utils/random-id'

describe('random UUID', () => {
  it('uses the native implementation when available', () => {
    const native = vi.fn(() => '550e8400-e29b-41d4-a716-446655440000')
    expect(randomUuid({ randomUUID: native })).toBe('550e8400-e29b-41d4-a716-446655440000')
    expect(native).toHaveBeenCalledOnce()
  })

  it('creates a UUID v4 from random bytes when randomUUID is unavailable', () => {
    const crypto = {
      getRandomValues: (bytes) => bytes.fill(0)
    }
    expect(randomUuid(crypto)).toBe('00000000-0000-4000-8000-000000000000')
  })
})
