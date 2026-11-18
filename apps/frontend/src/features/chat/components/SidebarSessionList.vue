<template>
  <div class="sidebar-session-list" :class="{ 'q-pl-sm': nested }">
    <q-resize-observer @resize="refreshVisibleList" />
    <div v-if="list.loading && !list.loaded" class="q-px-sm" role="status" aria-label="Sessions werden geladen">
      <q-skeleton v-for="index in 3" :key="index" type="text" height="32px" class="q-my-xs" />
    </div>
    <SidebarNavItem v-else-if="list.loadFailed && !list.loaded" icon="refresh" label="Erneut versuchen" @click="emit('retry')" />
    <q-list v-else class="session-list">
      <q-item v-if="list.loaded && !list.items.length" dense class="q-px-sm q-py-xs">
        <q-item-section>
          <q-item-label caption>Noch keine Sessions</q-item-label>
        </q-item-section>
      </q-item>
      <VirtualSessionRows v-if="rows.length" />
    </q-list>
    <div v-if="list.loadingMore" class="q-px-sm q-pb-xs" role="status" aria-label="Weitere Sessions werden geladen">
      <q-skeleton v-for="index in 3" :key="index" type="text" height="32px" class="q-my-xs" />
    </div>
    <div v-else-if="list.nextCursor || list.loadMoreFailed" class="q-pb-xs">
      <SidebarNavItem
        :icon="list.loadMoreFailed ? 'refresh' : 'expand_more'"
        :label="list.loadMoreFailed ? 'Erneut versuchen' : 'Mehr anzeigen'"
        @click="emit(list.loadMoreFailed ? 'retry-more' : 'load-more')"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { QItemLabel, QVirtualScroll, useQuasar } from 'quasar'
import SidebarNavItem from 'src/shared/ui/SidebarNavItem.vue'
import SidebarSessionItem from './SidebarSessionItem.vue'
import { groupSessionsByRecency } from 'src/features/chat/session-groups'

defineOptions({ name: 'SidebarSessionList' })

const props = defineProps({
  list: { type: Object, required: true },
  now: { type: Number, default: Date.now },
  activeSessionId: { type: String, default: null },
  statuses: { type: Map, default: () => new Map() },
  pinningSessionId: { type: String, default: null },
  scrollTarget: { type: [String, Object], default: '#app-navigation-drawer .session-scroll-area > .scroll' },
  nested: Boolean,
  showGroups: Boolean
})
const emit = defineEmits([
  'open',
  'rename',
  'move',
  'drag-start',
  'drag-end',
  'delete',
  'archive',
  'toggle-pin',
  'load-more',
  'retry-more',
  'retry'
])
const openActionMenuSessionId = ref(null)
const focusedSessionId = ref(null)
const draggedSessionId = ref(null)
const virtualScroll = ref(null)
const $q = useQuasar()
const rows = computed(() => {
  let position = 0
  return groupSessionsByRecency(props.list.items).flatMap((group) => [
    ...(props.showGroups ? [{ key: `group:${group.key}`, label: group.label }] : []),
    ...group.sessions.map((session) => ({ key: `session:${session.id}`, session, position: ++position }))
  ])
})
const sessionRows = computed(() => new Map(rows.value.flatMap((row, index) => row.session
  ? [[row.session.id, { row, index }]] : [])))
let sliceFrom = 0
let sliceTo = 0
let previousWidth = 0
let previousHeight = 0
let focusedElement = null
let focusedRowElement = null
let focusToRestore = null

onMounted(() => document.addEventListener('focusin', clearOutsideFocus, true))
onBeforeUnmount(() => document.removeEventListener('focusin', clearOutsideFocus, true))

// Flat keyed siblings let Quasar move an interaction's original DOM node out of
// the slice without unmounting its menu or native drag source.
function VirtualSessionRows() {
  return h(QVirtualScroll, {
    ref: virtualScroll,
    scrollTarget: props.scrollTarget,
    itemsSize: rows.value.length,
    itemsFn: (from, size) => {
      preserveSliceFocus()
      sliceFrom = from
      sliceTo = from + size
      return rows.value.slice(from, sliceTo)
    },
    virtualScrollItemSize: 30,
    virtualScrollSliceSize: 30
  }, {
    default: ({ item, index }) => [
      ...(index === sliceFrom ? retainedRows().map((row) => renderRow(row, true)) : []),
      renderRow(item)
    ]
  })
}

function retainedRows() {
  const retainedIds = new Set([openActionMenuSessionId.value, focusedSessionId.value, draggedSessionId.value])
  return [...retainedIds].flatMap((id) => {
    const entry = sessionRows.value.get(id)
    return entry && (entry.index < sliceFrom || entry.index >= sliceTo) ? [entry.row] : []
  })
}

function renderRow(row, retained = false) {
  const attributes = {
    key: row.key,
    class: retained ? 'sidebar-session-list__retained q-virtual-scroll--skip' : undefined
  }
  if (!row.session) {
    return h(QItemLabel, {
      ...attributes,
      header: true,
      class: ['sidebar-session-group-label q-pr-sm q-pt-sm q-pb-xs text-caption text-weight-medium',
        $q.dark.isActive ? 'text-grey-5' : 'text-grey-7']
    }, () => row.label)
  }
  const session = row.session
  return h(SidebarSessionItem, {
    ...attributes,
    session,
    now: props.now,
    active: props.activeSessionId === session.id,
    status: props.statuses.get(session.id),
    pinning: props.pinningSessionId === session.id,
    menuOpen: openActionMenuSessionId.value === session.id,
    'aria-posinset': row.position,
    'aria-setsize': props.list.items.length,
    'onUpdate:menuOpen': (open) => setActionMenu(session.id, open),
    onFocusin: (event) => {
      focusedSessionId.value = session.id
      focusedElement = event.target
      focusedRowElement = event.currentTarget
    },
    onFocusout: (event) => {
      if (!event.currentTarget.contains(event.relatedTarget)
        && !(focusToRestore === event.target
          && (event.relatedTarget === null || event.relatedTarget === focusedRowElement?.parentElement))) clearFocus()
    },
    onOpen: (value) => emit('open', value),
    onRename: (value) => emit('rename', value),
    onMove: (value) => emit('move', value),
    onArchive: (value) => emit('archive', value),
    onDelete: (value) => emit('delete', value),
    onTogglePin: (value) => emit('toggle-pin', value),
    onDragStart: (event) => {
      draggedSessionId.value = session.id
      emit('drag-start', event, session)
    },
    onDragEnd: () => {
      draggedSessionId.value = null
      emit('drag-end')
    }
  })
}

function clearFocus() {
  focusedSessionId.value = null
  focusedElement = null
  focusedRowElement = null
  focusToRestore = null
}

function clearOutsideFocus(event) {
  if (focusToRestore && event.target === focusedRowElement?.parentElement) return
  if (focusedRowElement && !focusedRowElement.contains(event.target)) clearFocus()
}

function preserveSliceFocus() {
  const target = focusedElement
  if (!target || document.activeElement !== target) return
  focusToRestore = target
  // Moving a keyed DOM node can blur even though Vue keeps the component alive.
  nextTick(() => {
    if (focusToRestore !== target) return
    focusToRestore = null
    if (target.isConnected && (document.activeElement === document.body
      || document.activeElement === focusedRowElement?.parentElement)) target.focus({ preventScroll: true })
  })
}

function refreshVisibleList({ width, height }) {
  if (height > 0 && (previousHeight === 0 || width !== previousWidth)) virtualScroll.value?.refresh(-1)
  previousWidth = width
  previousHeight = height
}

function setActionMenu(id, open) {
  if (open) openActionMenuSessionId.value = id
  else if (openActionMenuSessionId.value === id) openActionMenuSessionId.value = null
}
</script>

<style>
.sidebar-session-list .sidebar-session-list__retained {
  position: absolute;
  top: -10000px;
}
</style>
