export const MAX_STL_BYTES = 24 * 1024 * 1024
export const MAX_STL_TRIANGLES = 500_000

export class StlValidationError extends Error {
  constructor(code) {
    super(code)
    this.code = code
  }
}

export function validateStlBuffer(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 84) {
    throw new StlValidationError('invalid')
  }
  if (buffer.byteLength > MAX_STL_BYTES) throw new StlValidationError('tooLarge')

  const triangles = new DataView(buffer).getUint32(80, true)
  const expectedBytes = 84 + triangles * 50
  if (triangles > MAX_STL_TRIANGLES) throw new StlValidationError('tooComplex')
  if (expectedBytes !== buffer.byteLength) throw new StlValidationError('invalid')
}

export function validateStlGeometry(geometry) {
  const position = geometry?.getAttribute?.('position')
  const triangles = Number(position?.count) / 3
  if (!Number.isInteger(triangles) || triangles < 1) throw new StlValidationError('invalid')
  if (triangles > MAX_STL_TRIANGLES) throw new StlValidationError('tooComplex')
  for (const value of position.array) {
    if (!Number.isFinite(value)) throw new StlValidationError('invalid')
  }
  geometry.computeBoundingSphere()
  if (!Number.isFinite(geometry.boundingSphere?.radius) || geometry.boundingSphere.radius <= 0) {
    throw new StlValidationError('invalid')
  }
}
