import { onBeforeUnmount, ref } from 'vue'

export function useProjectReorder(projects, scrollArea) {
  const draggedProject = ref(null)
  const projectDropTarget = ref(null)
  const dragPosition = ref({ x: 0, y: 0 })
  let pending = null
  let holdTimer, scrollFrame
  let clickTimer

  function updateTarget() {
    const { x, y } = dragPosition.value
    const item = document.elementFromPoint(x, y)?.closest('.project-list-item')
    const id = item?.dataset.projectId
    const header = item?.querySelector('.project-list-item__header')
    projectDropTarget.value = header && pending.list.contains(item) && id !== draggedProject.value?.id
      ? { id, after: y > header.getBoundingClientRect().top + header.offsetHeight / 2 }
      : null
  }

  function scroll() {
    const target = scrollArea.value?.getScrollTarget()
    if (target) {
      const { top, bottom, left, right } = target.getBoundingClientRect()
      const { x, y } = dragPosition.value
      if (x >= left && x <= right && y >= top && y <= bottom) {
        const delta = y < top + 40 ? -8 : y > bottom - 40 ? 8 : 0
        if (delta) {
          target.scrollTop += delta
          updateTarget()
        }
      }
    }
    scrollFrame = requestAnimationFrame(scroll)
  }

  function activate() {
    draggedProject.value = projects.items.find((project) => project.id === pending.id)
    if (!draggedProject.value) return cancel()
    document.addEventListener('click', suppressProjectClick, true)
    document.addEventListener('contextmenu', suppressProjectClick, true)
    updateTarget()
    scrollFrame = requestAnimationFrame(scroll)
  }

  function cancel() {
    clearTimeout(holdTimer)
    cancelAnimationFrame(scrollFrame)
    document.removeEventListener('mousemove', move)
    document.removeEventListener('mouseup', finish)
    document.removeEventListener('touchmove', move)
    document.removeEventListener('touchend', finish)
    document.removeEventListener('touchcancel', cancel)
    window.removeEventListener('blur', cancel)
    pending = null
    draggedProject.value = null
    projectDropTarget.value = null
    // Keep the release click from toggling the expansion item.
    clickTimer = setTimeout(clearClickSuppression, 0)
  }

  function startProjectDrag(event) {
    if (pending) cancel()
    if (projects.reordering || (event.touches ? event.touches.length !== 1 : event.button !== 0)) return
    const header = event.target.closest('.project-list-item__header')
    if (!header || event.target.closest('.project-list-item__actions')) return
    clearTimeout(clickTimer)
    clearClickSuppression()
    const point = event.touches?.[0] ?? event
    dragPosition.value = { x: point.clientX, y: point.clientY }
    pending = {
      id: header.closest('[data-project-id]').dataset.projectId,
      list: event.currentTarget,
      x: point.clientX, y: point.clientY,
      touch: Boolean(event.touches)
    }
    if (pending.touch) {
      holdTimer = setTimeout(activate, 400)
      document.addEventListener('touchmove', move, { passive: false })
      document.addEventListener('touchend', finish)
      document.addEventListener('touchcancel', cancel)
    } else {
      document.addEventListener('mousemove', move)
      document.addEventListener('mouseup', finish)
    }
    window.addEventListener('blur', cancel)
  }

  function move(event) {
    if (event.touches && event.touches.length !== 1) return cancel()
    const point = event.touches?.[0] ?? event
    dragPosition.value = { x: point.clientX, y: point.clientY }
    if (!draggedProject.value) {
      if (Math.hypot(point.clientX - pending.x, point.clientY - pending.y) < 8) return
      if (pending.touch) return cancel()
      activate()
      if (!draggedProject.value) return
    }
    event.preventDefault()
    updateTarget()
  }

  async function finish(event) {
    const source = draggedProject.value
    const target = projectDropTarget.value
    if (source && event.cancelable) event.preventDefault()
    cancel()
    if (!source || !target) return
    const ids = projects.items.map((project) => project.id).filter((id) => id !== source.id)
    const index = ids.indexOf(target.id)
    if (index < 0 || !projects.items.some((project) => project.id === source.id)) return
    ids.splice(index + Number(target.after), 0, source.id)
    if (ids.every((id, position) => id === projects.items[position].id)) return
    try {
      await projects.reorder(ids)
    } catch {
      // The central Axios interceptor displays the error; the stored order stays unchanged.
    }
  }

  function suppressProjectClick(event) {
    event.preventDefault()
    event.stopPropagation()
  }

  function clearClickSuppression() {
    document.removeEventListener('click', suppressProjectClick, true)
    document.removeEventListener('contextmenu', suppressProjectClick, true)
  }

  onBeforeUnmount(() => { cancel(); clearTimeout(clickTimer); clearClickSuppression() })
  return { draggedProject, projectDropTarget, dragPosition, startProjectDrag }
}
