import { ref } from 'vue'

export function reorderedTabs(items, source, target, after, key) {
  const moved = items.find(item => key(item) === source)
  const remaining = items.filter(item => key(item) !== source)
  const index = remaining.findIndex(item => key(item) === target)
  if (!moved || index < 0 || source === target) return items
  remaining.splice(index + (after ? 1 : 0), 0, moved)
  return remaining.every((item, i) => item === items[i]) ? items : remaining
}

export function useTabReorder(reorder) {
  const draggedId = ref(null)
  const dropTarget = ref(null)
  function endDrag() {
    draggedId.value = null
    dropTarget.value = null
  }
  return {
    draggedId, dropTarget, endDrag,
    startDrag(event, id) {
      if (event.target.closest('button') || !event.dataTransfer) { event.preventDefault(); return }
      draggedId.value = id
      event.dataTransfer.effectAllowed = 'move'
      event.dataTransfer.setData('text/plain', id)
    },
    updateDropTarget(event, id) {
      if (!draggedId.value || draggedId.value === id) { dropTarget.value = null; return }
      event.preventDefault()
      const bounds = event.currentTarget.getBoundingClientRect()
      dropTarget.value = { id, after: event.clientX > bounds.left + bounds.width / 2 }
      event.dataTransfer.dropEffect = 'move'
    },
    async drop(event) {
      if (!draggedId.value || !dropTarget.value) return
      event.preventDefault()
      const source = draggedId.value, target = dropTarget.value
      endDrag()
      try { await reorder(source, target.id, target.after) } catch { /* Central API toast. */ }
    }
  }
}
