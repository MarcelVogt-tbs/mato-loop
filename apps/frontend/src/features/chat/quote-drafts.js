const QUOTE = '> [!QUOTE]'
const COMMENT = '> [!COMMENT]'

function codeFence(fence, line) {
  const delimiter = /^ {0,3}(`{3,}|~{3,})(.*)$/u.exec(line)
  if (!delimiter) return fence
  if (!fence) return delimiter[1]
  return delimiter[1][0] === fence[0] && delimiter[1].length >= fence.length && !delimiter[2].trim() ? null : fence
}

// Readable Markdown keeps quotes intact across existing draft, queue and undo flows.
export function quotedMessage(blocks) {
  const block = (marker, value) => [marker, ...value.split('\n').map(line => `> ${line}`)].join('\n')
  return blocks.filter(item => item.type === 'quote' || item.text).map((item, index, items) => {
    if (item.type === 'text') {
      const fence = items[index + 1]?.type === 'quote' ? item.text.split('\n').reduce(codeFence, null) : null
      return item.text + (fence ? `\n${fence}` : '')
    }
    return [block(QUOTE, item.text), ...(item.comment ? [block(COMMENT, item.comment)] : [])].join('\n\n')
  }).join('\n\n')
}

export function parseQuotedMessage(content = '') {
  const lines = content.split('\n')
  const blocks = []
  let cursor = 0
  let start = 0
  let fence = null
  function readBlock() {
    const start = ++cursor
    while (lines[cursor] === '>' || lines[cursor]?.startsWith('> ')) cursor += 1
    const value = lines.slice(start, cursor).map(line => line.slice(2)).join('\n')
    if (lines[cursor] === '') cursor += 1
    return value
  }
  while (cursor < lines.length) {
    const line = lines[cursor]
    fence = codeFence(fence, line)
    if (!fence && line === QUOTE) {
      const quoteStart = cursor
      const text = readBlock()
      if (text.trim()) {
        const preceding = lines.slice(start, quoteStart).join('\n')
        blocks.push({ type: 'text', text: preceding.endsWith('\n') ? preceding.slice(0, -1) : preceding })
        const comment = lines[cursor] === COMMENT ? readBlock() : ''
        blocks.push({ type: 'quote', text, comment })
        start = cursor
        continue
      }
      cursor = quoteStart
    }
    cursor += 1
  }
  blocks.push({ type: 'text', text: lines.slice(start).join('\n') })
  return blocks
}
