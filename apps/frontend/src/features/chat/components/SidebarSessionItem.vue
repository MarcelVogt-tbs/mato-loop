<template>
  <q-item
    clickable dense v-ripple
    class="session-list-item items-center rounded-borders q-px-sm q-py-xs q-my-xs"
    :class="{ 'session-list-item--menu-open': menuOpen }"
    :active="active"
    :active-class="$q.dark.isActive ? 'bg-grey-9 text-white' : 'bg-grey-3 text-dark'"
    @mouseenter="prepareTitleScroll"
    @focusin="prepareTitleScroll"
    @click="emit('open', session)"
  >
    <q-item-section
      class="session-list-item__content"
      :draggable="draggable"
      @dragstart="emit('drag-start', $event, session)"
      @dragend="emit('drag-end')"
    >
      <q-item-label
        class="session-list-item__title text-body2"
        :class="{ 'text-weight-bold': session.hasUnreadMessages, 'text-weight-medium': !session.hasUnreadMessages && active }"
      >
        <span class="session-list-item__title-text">{{ title || session.title }}</span>
        <span v-if="session.hasUnreadMessages" class="app-sr-only">, ungelesene Nachrichten</span>
      </q-item-label>
      <q-item-label v-if="caption" caption>{{ caption }}</q-item-label>
    </q-item-section>
    <q-item-section side class="session-list-item__actions row no-wrap items-center self-center">
      <span
        v-if="status"
        class="session-list-item__status-slot row items-center justify-center q-mr-xs"
        role="status"
        :aria-label="status.label"
      >
        <template v-if="status.kind === 'approval'">
          <q-icon name="pending_actions" color="warning" size="18px" />
          <q-badge v-if="status.count > 1" floating rounded color="warning" text-color="dark" :label="status.count" />
        </template>
        <q-spinner-dots v-else-if="status.kind === 'active'" color="primary" size="18px" />
        <q-tooltip v-if="$q.platform.is.mobile !== true">{{ status.label }}</q-tooltip>
      </span>
      <q-btn
        v-if="showPin"
        flat round dense size="sm" icon="push_pin"
        class="session-list-item__action session-list-item__pin-button"
        :class="{ 'session-list-item__pin-button--pinned': session.pinned }"
        :color="session.pinned ? 'primary' : ($q.dark.isActive ? 'grey-5' : 'grey-7')"
        :loading="pinning"
        :aria-label="session.pinned ? `${session.title} nicht mehr anheften` : `${session.title} anheften`"
        @click.stop="emit('toggle-pin', session)"
      >
        <q-tooltip>{{ session.pinned ? 'Nicht mehr anheften' : 'Anheften' }}</q-tooltip>
      </q-btn>
      <SidebarActionMenu
        v-model="menuOpen"
        class="session-list-item__action session-list-item__menu-button"
        :color="$q.dark.isActive ? 'grey-5' : 'grey-7'"
        :aria-label="`Aktionen für ${title || session.title}`"
        list-class="session-actions-menu"
      >
        <template v-if="session.title != null">
          <q-item clickable v-ripple v-close-popup @click="emit('rename', session)">
            <q-item-section avatar><q-icon name="edit" /></q-item-section>
            <q-item-section>Umbenennen</q-item-section>
          </q-item>
          <q-item clickable v-ripple v-close-popup @click="emit('move', session)">
            <q-item-section avatar><q-icon name="drive_file_move" /></q-item-section>
            <q-item-section>Verschieben</q-item-section>
          </q-item>
          <q-separator class="q-my-xs" />
          <q-item clickable v-ripple v-close-popup @click="emit('archive', session)">
            <q-item-section avatar><q-icon name="archive" /></q-item-section>
            <q-item-section>Archivieren</q-item-section>
          </q-item>
          <q-item
            clickable v-ripple v-close-popup class="text-negative"
            v-confirm="{ message: `Session „${session.title}“ löschen?`, title: 'Session löschen', okLabel: 'Löschen' }"
            @click="emit('delete', session)"
          >
            <q-item-section avatar><q-icon name="delete_outline" /></q-item-section>
            <q-item-section>Löschen</q-item-section>
          </q-item>
        </template>
        <q-item v-else><q-item-section>Session wird geladen …</q-item-section></q-item>
      </SidebarActionMenu>
    </q-item-section>
  </q-item>
</template>

<script setup>
import SidebarActionMenu from './SidebarActionMenu.vue'

defineProps({
  session: { type: Object, required: true },
  active: Boolean,
  title: { type: String, default: '' },
  caption: { type: String, default: '' },
  status: { type: Object, default: null },
  showPin: { type: Boolean, default: true },
  pinning: Boolean,
  draggable: { type: Boolean, default: true }
})
const menuOpen = defineModel('menuOpen', { type: Boolean, default: false })
const emit = defineEmits(['open', 'rename', 'move', 'archive', 'delete', 'toggle-pin', 'drag-start', 'drag-end'])

function prepareTitleScroll(event) {
  const title = event.currentTarget.querySelector('.session-list-item__title')
  const text = title.querySelector('.session-list-item__title-text')
  const distance = Math.max(0, text.getBoundingClientRect().width - title.clientWidth)
  title.style.setProperty('--session-title-scroll-distance', `${-distance}px`)
  title.style.setProperty('--session-title-scroll-duration', `${distance / 35}s`)
}
</script>
