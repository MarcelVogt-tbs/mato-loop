import { describe, expect, it } from 'vitest'

import {
  MAX_STL_BYTES,
  MAX_STL_TRIANGLES,
  validateStlBuffer,
  validateStlGeometry
} from '../src/utils/stl'

function binaryStl(triangles) {
  const buffer = new ArrayBuffer(84 + triangles * 50)
  new DataView(buffer).setUint32(80, triangles, true)
  return buffer
}

describe('STL viewer limits', () => {
  it('accepts bounded binary STL data and rejects oversized or excessive models', () => {
    expect(MAX_STL_BYTES).toBe(24 * 1024 * 1024)
    expect(MAX_STL_TRIANGLES).toBe(500_000)
    expect(() => validateStlBuffer(binaryStl(1))).not.toThrow()
    expect(() => validateStlBuffer(new ArrayBuffer(MAX_STL_BYTES + 1))).toThrow('tooLarge')
    const ascii = new Uint8Array(100)
    ascii.set(new TextEncoder().encode('solid model'))
    expect(() => validateStlBuffer(ascii.buffer)).toThrow('invalid')

    const header = new ArrayBuffer(84)
    new DataView(header).setUint32(80, MAX_STL_TRIANGLES + 1, true)
    expect(() => validateStlBuffer(header)).toThrow('tooComplex')
  })

  it('rejects empty and non-finite parsed geometry', () => {
    const geometry = (values) => ({
      getAttribute: () => ({ count: values.length / 3, array: values }),
      computeBoundingSphere() {
        this.boundingSphere = { radius: 1 }
      }
    })
    expect(() => validateStlGeometry(geometry(new Float32Array(9)))).not.toThrow()
    expect(() => validateStlGeometry(geometry(new Float32Array([0, 0, 0])))).toThrow('invalid')
    expect(() => validateStlGeometry(geometry(new Float32Array([0, 0, 0, 1, 0, 0, Number.NaN, 1, 0]))))
      .toThrow('invalid')
  })
})
