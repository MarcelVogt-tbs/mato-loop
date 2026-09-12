export function encodedInputChunks(value) {
  const bytes = new TextEncoder().encode(value)
  const chunks = []
  for (let offset = 0; offset < bytes.length; offset += 65_536) {
    chunks.push(btoa(Array.from(bytes.subarray(offset, offset + 65_536), (byte) => String.fromCharCode(byte)).join('')))
  }
  return chunks
}
