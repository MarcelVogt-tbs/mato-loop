<template>
  <div class="sidebar-session-list" :class="{ 'q-pl-sm': nested }">
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
      <template v-for="group in groupedSessions" :key="group.key">
        <q-item-label
          v-if="showGroups"
          header
          class="sidebar-session-group-label q-pr-sm q-pt-sm q-pb-xs text-caption text-weight-medium"
          :class="$q.dark.isActive ? 'text-grey-5' : 'text-grey-7'"
        >
          {{ group.label }}
        </q-item-label>
        <SidebarSessionItem
          v-for="session in group.sessions"
          :key="session.id"
          :session="session"
          :now="now"
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
import { computed, ref } from 'vue'
import SidebarNavItem from 'src/shared/ui/SidebarNavItem.vue'
import SidebarSessionItem from './SidebarSessionItem.vue'
import { groupSessionsByRecency } from 'src/utils/session-groups'

defineOptions({ name: 'SidebarSessionList' })

const props = defineProps({
  list: { type: Object, required: true },
  now: { type: Number, default: Date.now },
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
