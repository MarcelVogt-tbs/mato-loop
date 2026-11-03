export function quotePosition(bounds, selection, size) {
  const gap = 8
  const above = {
    space: Math.max(0, Math.min(selection.top, bounds.bottom) - bounds.top - gap * 2),
    bottom: Math.min(selection.top, bounds.bottom) - gap
  }
  const below = {
    space: Math.max(0, bounds.bottom - Math.max(selection.bottom, bounds.top) - gap * 2),
    top: Math.max(selection.bottom, bounds.top) + gap
  }
  const candidates = selection.preferAbove ? [above, below] : [below, above]
  const side = candidates.find(candidate => candidate.space >= size.height)
    ?? (above.space >= below.space ? above : below)
  const top = side === above ? above.bottom - Math.min(size.height, side.space) : below.top
  const left = (selection.left + selection.right - size.width) / 2
  return {
    top: `${top}px`,
    left: `${Math.min(Math.max(left, bounds.left + gap), Math.max(bounds.left + gap, bounds.right - size.width - gap))}px`,
    maxHeight: `${side.space}px`
  }
}
