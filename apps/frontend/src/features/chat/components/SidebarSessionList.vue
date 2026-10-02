<template>
  <div class="sidebar-session-list" :class="{ 'sidebar-session-list--nested': nested }">
    <div v-if="list.loading && !list.loaded" class="q-px-sm" role="status" aria-label="Sessions werden geladen">
      <q-skeleton v-for="index in 3" :key="index" type="text" height="32px" class="q-my-xs" />
    </div>
    <div v-else-if="list.loadFailed && !list.loaded" class="q-pa-sm">
      <q-btn
        flat
        dense
        no-caps
        class="full-width"
        color="primary"
        icon-right="refresh"
        label="Erneut versuchen"
        @click="emit('retry')"
      />
    </div>
    <q-list v-else class="session-list">
      <q-item v-if="list.loaded && !list.items.length" dense class="q-px-sm q-py-xs">
        <q-item-section>
          <q-item-label caption>Noch keine Sessions</q-item-label>
        </q-item-section>
      </q-item>
      <template v-for="group in groupedSessions" :key="group.key">
        <q-item-label
          v-if="showGroups"
          header
          class="q-px-sm q-pt-md q-pb-xs text-caption text-weight-medium"
          :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
        >
          {{ group.label }}
        </q-item-label>
        <SidebarSessionItem
          v-for="session in group.sessions"
          :key="session.id"
          :session="session"
          :active="activeSessionId === session.id"
          :status="statuses.get(session.id)"
          :pinning="pinningSessionId === session.id"
          :menu-open="openActionMenuSessionId === session.id"
          @update:menu-open="setActionMenu(session.id, $event)"
          @open="emit('open', $event)"
          @rename="emit('rename', $event)"
          @move="emit('move', $event)"
          @archive="emit('archive', $event)"
          @delete="emit('delete', $event)"
          @toggle-pin="emit('toggle-pin', $event)"
          @drag-start="emit('drag-start', $event, session)"
          @drag-end="emit('drag-end')"
        />
      </template>
    </q-list>
    <div v-if="list.loadingMore" class="q-px-sm q-pb-xs" role="status" aria-label="Weitere Sessions werden geladen">
      <q-skeleton v-for="index in 3" :key="index" type="text" height="32px" class="q-my-xs" />
    </div>
    <div v-else-if="list.nextCursor || list.loadMoreFailed" class="q-px-sm q-pb-xs">
      <q-btn
        flat
        dense
        no-caps
        color="primary"
        :icon-right="list.loadMoreFailed ? 'refresh' : undefined"
        :label="list.loadMoreFailed ? 'Erneut versuchen' : 'Mehr anzeigen'"
        @click="emit(list.loadMoreFailed ? 'retry-more' : 'load-more')"
      />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import SidebarSessionItem from './SidebarSessionItem.vue'
import { groupSessionsByRecency } from 'src/utils/session-groups'

defineOptions({ name: 'SidebarSessionList' })

const props = defineProps({
  list: { type: Object, required: true },
  activeSessionId: { type: String, default: null },
  statuses: { type: Map, default: () => new Map() },
  pinningSessionId: { type: String, default: null },
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
const groupedSessions = computed(() => groupSessionsByRecency(props.list.items))

function setActionMenu(id, open) {
  if (open) openActionMenuSessionId.value = id
  else if (openActionMenuSessionId.value === id) openActionMenuSessionId.value = null
}
</script>
