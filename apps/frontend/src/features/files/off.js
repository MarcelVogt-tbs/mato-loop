export const MAX_OFF_BYTES = 24 * 1024 * 1024
export const MAX_OFF_LINE_LENGTH = 1024 * 1024
export const MAX_OFF_VERTICES = 500_000
export const MAX_OFF_TRIANGLES = 500_000

export class OffValidationError extends Error {
  constructor(code) {
    super(code)
    this.code = code
  }
}

export function parseOffBuffer(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 16) throw new OffValidationError('invalid')
  if (buffer.byteLength > MAX_OFF_BYTES) throw new OffValidationError('tooLarge')

  let text
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(buffer)
  } catch {
    throw new OffValidationError('invalid')
  }
  const rawLines = text.split(/\r?\n/)
  if (rawLines.some((line) => line.length > MAX_OFF_LINE_LENGTH)) {
    throw new OffValidationError('invalid')
  }
  const lines = rawLines.map((line) => line.trim()).filter(Boolean)
  const header = lines[0]?.match(/^OFF(?:\s+(.*))?$/u)
  if (!header) throw new OffValidationError('invalid')
  if (header[1]) lines.splice(0, 1, 'OFF', header[1])
  const counts = lines[1]?.split(/\s+/).map(Number)
  if (counts?.length !== 3 || counts.some((value) => !Number.isSafeInteger(value) || value < 0)) {
    throw new OffValidationError('invalid')
  }
  const [vertexCount, faceCount] = counts
  if (
    vertexCount < 3 || vertexCount > MAX_OFF_VERTICES
    || faceCount < 1 || faceCount > MAX_OFF_TRIANGLES
    || lines.length !== 2 + vertexCount + faceCount
  ) {
    throw new OffValidationError('invalid')
  }

  const vertices = lines.slice(2, 2 + vertexCount).map((line) => {
    const values = line.split(/\s+/).map(Number)
    if (values.length !== 3 || values.some((value) => !Number.isFinite(value))) {
      throw new OffValidationError('invalid')
    }
    return values
  })
  const positions = []
  const colors = []
  let hasColors = false
  let triangleCount = 0
  for (const line of lines.slice(2 + vertexCount)) {
    const values = line.split(/\s+/).map(Number)
    const count = values[0]
    if (!Number.isSafeInteger(count) || count < 3 || ![count + 1, count + 4, count + 5].includes(values.length)) {
      throw new OffValidationError('invalid')
    }
    const indices = values.slice(1, count + 1)
    if (indices.some((index) => !Number.isSafeInteger(index) || index < 0 || index >= vertexCount)) {
      throw new OffValidationError('invalid')
    }
    triangleCount += count - 2
    if (triangleCount > MAX_OFF_TRIANGLES) throw new OffValidationError('tooComplex')
    const color = values.length > count + 1
      ? values.slice(count + 1, count + 4).map((value) => value / 255)
      : null
    if (color?.some((value) => !Number.isFinite(value) || value < 0 || value > 1)) {
      throw new OffValidationError('invalid')
    }
    if (color) hasColors = true
    for (let index = 1; index < count - 1; index += 1) {
      for (const vertexIndex of [indices[0], indices[index], indices[index + 1]]) {
        positions.push(...vertices[vertexIndex])
        colors.push(...(color || [0.2588, 0.6471, 0.9608]))
      }
    }
  }
  if (!triangleCount) throw new OffValidationError('invalid')
  return {
    positions: new Float32Array(positions),
    colors: hasColors ? new Float32Array(colors) : null,
    triangleCount
  }
}
