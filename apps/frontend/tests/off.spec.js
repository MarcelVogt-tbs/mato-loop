import { describe, expect, it } from 'vitest'

import {
  MAX_OFF_BYTES,
  MAX_OFF_LINE_LENGTH,
  MAX_OFF_TRIANGLES,
  MAX_OFF_VERTICES,
  parseOffBuffer
} from '../src/utils/off'

function buffer(content) {
  return new TextEncoder().encode(content).buffer
}

describe('OFF viewer limits', () => {
  it('triangulates bounded colored faces', () => {
    const parsed = parseOffBuffer(buffer([
      'OFF 4 1 0',
      '0 0 0',
      '1 0 0',
      '1 1 0',
      '0 1 0',
      '4 0 1 2 3 66 165 245'
    ].join('\n')))

    expect(MAX_OFF_BYTES).toBe(24 * 1024 * 1024)
    expect(MAX_OFF_LINE_LENGTH).toBe(1024 * 1024)
    expect(MAX_OFF_VERTICES).toBe(500_000)
    expect(MAX_OFF_TRIANGLES).toBe(500_000)
    expect(parsed.triangleCount).toBe(2)
    expect(parsed.positions).toHaveLength(18)
    expect(parsed.colors).toHaveLength(18)
  })

  it('rejects invalid indices, coordinates, and excessive faces', () => {
    expect(() => parseOffBuffer(buffer('OFF\n3 1 0\n0 0 0\n1 0 0\n0 1 0\n3 0 1 3'))).toThrow('invalid')
    expect(() => parseOffBuffer(buffer('OFF\n3 1 0\n0 0 0\nNaN 0 0\n0 1 0\n3 0 1 2'))).toThrow('invalid')
    expect(() => parseOffBuffer(buffer(`OFF ${' '.repeat(MAX_OFF_LINE_LENGTH + 1)}`))).toThrow('invalid')
    expect(() => parseOffBuffer(new ArrayBuffer(MAX_OFF_BYTES + 1))).toThrow('tooLarge')
  })
})
