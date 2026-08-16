import { describe, expect, it } from 'vitest'

import {
  chatBottomDistance,
  chatBottomOffset,
  scrollChatElementToTop,
  scrollChatToBottom
} from '../src/utils/chat-scroll'

describe('chat scroll geometry', () => {
  it('calculates the exact bottom offset without an oversized sentinel value', () => {
    const target = { clientHeight: 640, scrollHeight: 1840, scrollTop: 0 }

    expect(chatBottomOffset(target)).toBe(1200)
  })

  it('calculates the remaining distance and clamps non-scrollable content', () => {
    expect(chatBottomDistance({ clientHeight: 640, scrollHeight: 1840, scrollTop: 1176 })).toBe(24)
    expect(chatBottomDistance({ clientHeight: 640, scrollHeight: 500, scrollTop: 0 })).toBe(0)
    expect(chatBottomOffset({ clientHeight: 640, scrollHeight: 500 })).toBe(0)
  })

  it('moves the scroll target to the current bottom immediately', () => {
    const target = { clientHeight: 640, scrollHeight: 1920, scrollTop: 1200 }

    scrollChatToBottom(target)

    expect(target.scrollTop).toBe(1280)
  })

  it('aligns expanded chat content at the top and respects reduced motion', () => {
    const calls = []
    const element = { scrollIntoView: (options) => calls.push(options) }

    scrollChatElementToTop(element)
    scrollChatElementToTop(element, false)

    expect(calls).toEqual([
      { behavior: 'smooth', block: 'start' },
      { behavior: 'auto', block: 'start' }
    ])
  })
})
