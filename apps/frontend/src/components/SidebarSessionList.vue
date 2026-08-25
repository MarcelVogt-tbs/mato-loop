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
        <q-item
          v-for="session in group.sessions"
          :key="session.id"
          clickable
          dense
          v-ripple
          class="session-list-item items-center rounded-borders q-px-sm q-py-xs q-my-xs"
          :class="{ 'session-list-item--menu-open': openActionMenuSessionId === session.id }"
          :active="activeSessionId === session.id"
          :active-class="$q.dark.isActive ? 'bg-grey-9 text-white' : 'bg-grey-3 text-dark'"
          @click="emit('open', session)"
        >
          <q-item-section class="session-list-item__content">
            <q-item-label
              lines="1"
              class="session-list-item__title text-body2"
              :class="{
                'text-weight-bold': session.hasUnreadMessages,
                'text-weight-medium': !session.hasUnreadMessages && activeSessionId === session.id
              }"
            >
              {{ session.title }}
              <span v-if="session.hasUnreadMessages" class="app-sr-only">, ungelesene Nachrichten</span>
            </q-item-label>
            <q-tooltip
              v-if="$q.platform.is.mobile !== true"
              :delay="700"
              anchor="bottom middle"
              self="top middle"
              :offset="[0, 8]"
              class="session-title-tooltip"
            >
              <div>{{ session.title }}</div>
              <div v-if="session.currentDirectory" class="q-mt-xs text-grey-4">
                {{ session.currentDirectory }}
              </div>
            </q-tooltip>
          </q-item-section>
          <q-item-section side class="session-list-item__actions row no-wrap items-center self-center">
            <span
              v-if="statuses.get(session.id)"
              class="session-list-item__status-slot row items-center justify-center q-mr-xs"
              role="status"
              :aria-label="statuses.get(session.id)?.label"
            >
              <template v-if="statuses.get(session.id)?.kind === 'approval'">
                <q-icon name="pending_actions" color="warning" size="18px" />
                <q-badge
                  v-if="statuses.get(session.id).count > 1"
                  floating
                  rounded
                  color="warning"
                  text-color="dark"
                  :label="statuses.get(session.id).count"
                />
              </template>
              <q-spinner-dots
                v-else-if="statuses.get(session.id)?.kind === 'active'"
                color="primary"
                size="18px"
              />
              <q-tooltip v-if="$q.platform.is.mobile !== true">
                {{ statuses.get(session.id).label }}
              </q-tooltip>
            </span>
            <q-btn
              flat
              round
              dense
              size="sm"
              icon="push_pin"
              class="session-list-item__action session-list-item__pin-button"
              :class="{ 'session-list-item__pin-button--pinned': session.pinned }"
              :color="session.pinned ? 'primary' : ($q.dark.isActive ? 'grey-5' : 'grey-7')"
              :loading="pinningSessionId === session.id"
              :aria-label="session.pinned ? `${session.title} nicht mehr anheften` : `${session.title} anheften`"
              @click.stop="emit('toggle-pin', session)"
            >
              <q-tooltip>{{ session.pinned ? 'Nicht mehr anheften' : 'Anheften' }}</q-tooltip>
            </q-btn>
            <q-btn
              flat
              round
              dense
              size="sm"
              icon="more_horiz"
              class="session-list-item__action session-list-item__menu-button"
              :color="$q.dark.isActive ? 'grey-5' : 'grey-7'"
              :aria-label="`Aktionen für ${session.title}`"
              @click.stop
            >
              <q-menu
                anchor="bottom right"
                self="top right"
                :offset="[0, 8]"
                class="app-action-menu"
                @before-show="openActionMenuSessionId = session.id"
                @hide="closeActionMenu(session.id)"
              >
                <q-list padding class="session-actions-menu">
                  <q-item clickable v-ripple v-close-popup @click="emit('rename', session)">
                    <q-item-section avatar><q-icon name="edit" /></q-item-section>
                    <q-item-section>Umbenennen</q-item-section>
                  </q-item>
                  <q-item clickable v-ripple v-close-popup @click="emit('move', session)">
                    <q-item-section avatar><q-icon name="drive_file_move" /></q-item-section>
                    <q-item-section>Verschieben</q-item-section>
                  </q-item>
                  <q-separator class="q-my-xs" />
                  <q-item
                    clickable
                    v-ripple
                    v-close-popup
                    class="text-negative"
                    v-confirm="{ message: `Session „${session.title}“ löschen?`, title: 'Session löschen', okLabel: 'Löschen' }"
                    @click="emit('delete', session)"
                  >
                    <q-item-section avatar><q-icon name="delete_outline" /></q-item-section>
                    <q-item-section>Löschen</q-item-section>
                  </q-item>
                </q-list>
              </q-menu>
            </q-btn>
          </q-item-section>
        </q-item>
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
  'delete',
  'toggle-pin',
  'load-more',
  'retry-more',
  'retry'
])
const openActionMenuSessionId = ref(null)
const groupedSessions = computed(() => groupSessionsByRecency(props.list.items))

function closeActionMenu(id) {
  if (openActionMenuSessionId.value === id) openActionMenuSessionId.value = null
}
</script>
