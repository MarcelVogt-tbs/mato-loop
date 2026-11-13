import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  chatBottomDistance,
  chatReadingAnchor,
  chatShouldFollowBottom,
  restoreChatReadingAnchor,
  scrollChatToBottom
} from './scroll'

export function useChatViewport({ chat, route, scrollArea, scrollContent, virtualScroll, displayMessages, setWorkHistoryExpanded }) {
  const routeSessionId = computed(() => typeof route.query.session === 'string' ? route.query.session : null)
  const routeMessageId = computed(() => typeof route.query.message === 'string' ? route.query.message : null)
  const followingBottom = ref(true)
  const transcriptPreparing = ref(Boolean(chat.current))
  const highlightedMessageId = ref(null)
  let target, observer, operation, scheduledFrame, userScrollTimer, highlightTimer
  let disposed = false
  let pending = false
  let resetSizes = false
  let revealMessageId = null
  let readingAnchor = null
  let userScrolling = false
  let touching = false
  let lastScrollTop = 0

  const elements = () => [...(scrollContent.value?.querySelectorAll('[data-message-id]') || [])]
  const findElement = id => elements().find(element => element.dataset.messageId === id || element.dataset.renderId === id)
  const captureAnchor = () => chatReadingAnchor(target, elements())
  const isCurrent = request => !disposed && operation === request && !chat.loading
    && virtualScroll.value === request.virtualScroll

  function cancelLayout() {
    clearTimeout(highlightTimer)
    highlightedMessageId.value = null
    if (scheduledFrame != null) window.cancelAnimationFrame(scheduledFrame)
    scheduledFrame = null
    if (operation?.frame != null) window.cancelAnimationFrame(operation.frame)
    operation?.resume?.()
    operation = null
  }

  async function rendered(request) {
    await nextTick()
    if (!isCurrent(request)) return false
    await new Promise(resolve => {
      request.resume = resolve
      request.frame = window.requestAnimationFrame(() => {
        request.frame = null
        request.resume = null
        resolve()
      })
    })
    await nextTick()
    return isCurrent(request)
  }

  function scheduleTranscriptLayout(reset = false) {
    if (disposed) return
    pending = true
    resetSizes ||= reset
    if (userScrolling || operation || scheduledFrame != null) return
    scheduledFrame = window.requestAnimationFrame(() => {
      scheduledFrame = null
      void layoutTranscript()
    })
  }

  // Search, initial positioning, follow mode and anchor restoration share this operation.
  // Virtual-scroll events are notifications, not completion promises: reset may emit none.
  async function layoutTranscript() {
    if (disposed || userScrolling || chat.loading || !virtualScroll.value
      || revealMessageId && chat.current?.id !== routeSessionId.value) return
    const request = {
      virtualScroll: virtualScroll.value,
      anchor: followingBottom.value ? null : readingAnchor || captureAnchor()
    }
    operation = request
    pending = false
    const messageId = revealMessageId
    try {
      const matches = message => message.id === messageId || message.workHistory?.entries?.some(entry => (
        entry.message?.id === messageId || entry.batchId === messageId
      ))
      let searchIndex = messageId ? displayMessages.value.findIndex(matches) : -1
      while (messageId && searchIndex < 0 && chat.current?.nextMessageCursor) {
        const cursor = chat.current.nextMessageCursor
        try { await chat.loadOlderMessages(chat.current.id) } catch { break /* The API interceptor reports failures. */ }
        await nextTick()
        if (!isCurrent(request)) return
        searchIndex = displayMessages.value.findIndex(matches)
        if (chat.current?.nextMessageCursor === cursor) break
      }
      const searchedMessage = searchIndex >= 0 ? displayMessages.value[searchIndex] : null
      if (searchedMessage) {
        followingBottom.value = false
        readingAnchor = request.anchor = null
        highlightedMessageId.value = searchedMessage.id
        if (searchedMessage.id !== messageId) {
          setWorkHistoryExpanded(searchedMessage.workHistory.id || searchedMessage.id, true)
        }
      }
      await nextTick()
      if (!isCurrent(request)) return
      request.refreshing = true
      if (resetSizes) virtualScroll.value.reset?.()
      else virtualScroll.value.refresh?.(-1)
      request.refreshing = false
      resetSizes = false
      if (!await rendered(request)) return
      const messages = displayMessages.value
      const anchorIndex = request.anchor
        ? messages.findIndex(message => (message.renderId || message.id) === request.anchor.id) : -1
      const index = searchedMessage ? messages.findIndex(matches) : followingBottom.value ? messages.length - 1 : anchorIndex
      const id = searchedMessage?.id || (index >= 0 ? messages[index].renderId || messages[index].id : null)
      if (index >= 0 && (searchedMessage || !findElement(id))) {
        virtualScroll.value?.scrollTo(index, searchedMessage ? 'center-force' : followingBottom.value ? 'end-force' : 'start-force')
        if (!await rendered(request)) return
      }
      const element = id ? findElement(id) : null
      // Do not uncover an initial transcript before Quasar has rendered its destination.
      if (index >= 0 && !element) {
        return
      }
      if (searchedMessage && element && target) {
        restoreChatReadingAnchor(target, element, {
          offsetTop: Math.max(0, (target.clientHeight - element.getBoundingClientRect().height) / 2)
        })
      } else if (followingBottom.value) scrollChatToBottom(target)
      else restoreChatReadingAnchor(target, element, request.anchor)
      lastScrollTop = target?.scrollTop || 0
      transcriptPreparing.value = false
      // Focus only after Vue has removed inert, never while initial positioning is hidden.
      await nextTick()
      if (!isCurrent(request)) return
      if (searchedMessage) {
        element?.focus({ preventScroll: true })
        clearTimeout(highlightTimer)
        highlightTimer = window.setTimeout(() => { highlightedMessageId.value = null }, 2500)
      }
      if (revealMessageId === messageId) revealMessageId = null
      if (!followingBottom.value) readingAnchor = captureAnchor() || request.anchor
    } finally {
      if (isCurrent(request)) {
        operation = null
        if (pending) scheduleTranscriptLayout()
      }
    }
  }

  async function loadOlderHistory() {
    if (!chat.current?.nextMessageCursor || chat.olderMessagesLoading) return
    try { await chat.loadOlderMessages() } catch { /* The store exposes the retry state. */ }
  }

  function handleVirtualScroll({ from, direction }) {
    if (operation && !operation.refreshing) pending = true
    if (!operation && !followingBottom.value) readingAnchor = captureAnchor() || readingAnchor
    if (!operation && (transcriptPreparing.value || revealMessageId)) scheduleTranscriptLayout()
    if (userScrolling && direction === 'decrease' && from <= 3 && !followingBottom.value) void loadOlderHistory()
  }

  function beginActivityResize() {
    if (!chat.current) return
    cancelLayout()
    revealMessageId = null
    followingBottom.value = false
    readingAnchor = captureAnchor()
  }

  function updateFollowState() {
    const movedUp = target.scrollTop < lastScrollTop
    lastScrollTop = target.scrollTop
    if (operation && !userScrolling) return
    followingBottom.value = chatShouldFollowBottom(
      followingBottom.value, userScrolling && movedUp, userScrolling && chatBottomDistance(target) <= 64
    )
    if (!operation) readingAnchor = followingBottom.value ? null : captureAnchor() || readingAnchor
    if (userScrolling && !touching) scheduleUserScrollEnd()
  }

  function beginUserScroll() {
    cancelLayout()
    revealMessageId = null
    userScrolling = true
    clearTimeout(userScrollTimer)
  }

  function scheduleUserScrollEnd() {
    clearTimeout(userScrollTimer)
    userScrollTimer = window.setTimeout(() => {
      userScrolling = false
      // Momentum scrolling can continue after touchend; capture only when it settles.
      if (!followingBottom.value) readingAnchor = captureAnchor()
      scheduleTranscriptLayout()
    }, 180)
  }

  function handleTouchStart() { touching = true; beginUserScroll() }
  function handleTouchMove() { if (!userScrolling) beginUserScroll() }
  function handleTouchEnd() { touching = false; if (userScrolling) scheduleUserScrollEnd() }
  function handleWheel(event) {
    if (!event.deltaY) return
    beginUserScroll()
    scheduleUserScrollEnd()
  }
  function handleScrollKey(event) {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey
      || event.target?.closest?.('input, textarea, select, [contenteditable="true"]')
      || !['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)) return
    beginUserScroll()
    scheduleUserScrollEnd()
  }
  function handleScrollAreaMouseDown(event) {
    if (event.button !== 0 || !target || target.contains(event.target)) return
    beginUserScroll()
    window.removeEventListener('mouseup', scheduleUserScrollEnd)
    window.addEventListener('mouseup', scheduleUserScrollEnd, { once: true })
  }

  function enableFollowMode() {
    cancelLayout()
    revealMessageId = null
    touching = userScrolling = false
    clearTimeout(userScrollTimer)
    followingBottom.value = true
    readingAnchor = null
    scheduleTranscriptLayout()
  }

  watch([() => chat.current?.id, routeSessionId, routeMessageId], ([sessionId], previous = []) => {
    cancelLayout()
    revealMessageId = routeMessageId.value
    if (sessionId !== previous[0]) {
      clearTimeout(userScrollTimer)
      window.removeEventListener('mouseup', scheduleUserScrollEnd)
      touching = userScrolling = false
      readingAnchor = null
      followingBottom.value = true
      transcriptPreparing.value = Boolean(sessionId)
      resetSizes = true
    }
    scheduleTranscriptLayout()
  }, { immediate: true, flush: 'sync' })
  watch(displayMessages, () => scheduleTranscriptLayout(), { flush: 'pre' })
  watch(() => chat.loading, loading => {
    if (loading) cancelLayout()
    else scheduleTranscriptLayout()
  }, { flush: 'sync' })
  watch(() => chat.activeContext.sessionProfile?.messageSize || 'standard', () => scheduleTranscriptLayout(true), { flush: 'pre' })
  watch(virtualScroll, () => {
    cancelLayout()
    scheduleTranscriptLayout()
  }, { flush: 'post' })

  const listeners = {
    scroll: updateFollowState, touchstart: handleTouchStart, touchmove: handleTouchMove,
    touchend: handleTouchEnd, touchcancel: handleTouchEnd, wheel: handleWheel
  }
  onMounted(() => {
    target = scrollArea.value?.getScrollTarget?.() || null
    lastScrollTop = target?.scrollTop || 0
    for (const [event, listener] of Object.entries(listeners)) target?.addEventListener(event, listener, { passive: true })
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => scheduleTranscriptLayout())
      if (scrollContent.value) observer.observe(scrollContent.value)
      if (target) observer.observe(target)
    }
    scheduleTranscriptLayout()
  })
  onBeforeUnmount(() => {
    disposed = true
    cancelLayout()
    observer?.disconnect()
    for (const [event, listener] of Object.entries(listeners)) target?.removeEventListener(event, listener)
    window.removeEventListener('mouseup', scheduleUserScrollEnd)
    clearTimeout(userScrollTimer)
    clearTimeout(highlightTimer)
  })

  return {
    followingBottom, transcriptPreparing, highlightedMessageId,
    scheduleTranscriptLayout, loadOlderHistory, enableFollowMode,
    beginActivityResize, finishActivityResize: () => scheduleTranscriptLayout(),
    handleMessageRendered: () => scheduleTranscriptLayout(), handleVirtualScroll,
    handleScrollKey, handleScrollAreaMouseDown,
    scrollToTop: () => { if (target) target.scrollTop = 0 }
  }
}
