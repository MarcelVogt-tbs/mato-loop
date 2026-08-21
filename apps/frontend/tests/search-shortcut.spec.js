import { describe, expect, it } from 'vitest'

import { searchShortcutAction } from '../src/utils/search-shortcut'

describe('global search shortcut', () => {
  it.each([
    { ctrlKey: true, metaKey: false },
    { ctrlKey: false, metaKey: true }
  ])('opens MatoLoop search with Ctrl or Command', (modifiers) => {
    expect(searchShortcutAction({ key: 'f', ...modifiers }, false)).toBe('app')
  })

  it('returns the second shortcut to the browser', () => {
    expect(searchShortcutAction({ key: 'F', ctrlKey: true }, true)).toBe('browser')
  })

  it('ignores unrelated, modified, repeated, and already handled keys', () => {
    expect(searchShortcutAction({ key: 'f' }, false)).toBeNull()
    expect(searchShortcutAction({ key: 'f', ctrlKey: true, shiftKey: true }, false)).toBeNull()
    expect(searchShortcutAction({ key: 'f', metaKey: true, repeat: true }, false)).toBeNull()
    expect(searchShortcutAction({ key: 'f', ctrlKey: true, defaultPrevented: true }, false)).toBeNull()
  })
})
