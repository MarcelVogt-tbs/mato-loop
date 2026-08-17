<template>
  <q-toolbar class="q-px-md">
    <q-icon :name="icon" color="primary" size="26px" class="q-mr-sm" />
    <q-toolbar-title>
      <div :id="titleId" class="text-subtitle1 text-weight-bold">{{ title }}</div>
      <div v-if="subtitle" class="text-caption app-muted">{{ subtitle }}</div>
    </q-toolbar-title>
    <slot name="actions" />
    <q-btn
      v-if="refreshable"
      flat
      round
      dense
      icon="refresh"
      :aria-label="refreshLabel"
      :loading="refreshing"
      :disable="refreshDisabled"
      @click="emit('refresh')"
    >
      <q-tooltip>{{ refreshLabel }}</q-tooltip>
    </q-btn>
    <q-btn v-if="hasMenu" flat round dense icon="more_vert" :aria-label="`${title}: Weitere Aktionen`">
      <q-tooltip>Weitere Aktionen</q-tooltip>
      <q-menu anchor="bottom right" self="top right" class="app-action-menu">
        <q-list padding>
          <slot name="menu" />
          <q-separator v-if="customMenu && integrationId" spaced />
          <template v-if="integrationId">
            <template v-if="canMoveLeft || canMoveRight">
              <q-item clickable v-ripple v-close-popup :disable="!canMoveLeft" @click="emit('move', -1)">
                <q-item-section avatar><q-icon name="arrow_back" /></q-item-section>
                <q-item-section>Nach links verschieben</q-item-section>
              </q-item>
              <q-item clickable v-ripple v-close-popup :disable="!canMoveRight" @click="emit('move', 1)">
                <q-item-section avatar><q-icon name="arrow_forward" /></q-item-section>
                <q-item-section>Nach rechts verschieben</q-item-section>
              </q-item>
              <q-separator spaced />
            </template>
            <q-item clickable v-ripple v-close-popup class="text-negative" @click="emit('remove')">
              <q-item-section avatar><q-icon name="remove_circle_outline" /></q-item-section>
              <q-item-section>Integration aus Session entfernen</q-item-section>
            </q-item>
          </template>
        </q-list>
      </q-menu>
    </q-btn>
    <q-btn flat round dense icon="close" aria-label="Control Center schließen" @click="emit('close')">
      <q-tooltip>Control Center schließen</q-tooltip>
    </q-btn>
  </q-toolbar>
  <q-separator />
</template>

<script setup>
import { computed } from 'vue'

defineOptions({ name: 'ControlCenterContentHeader' })

const props = defineProps({
  icon: { type: String, required: true },
  title: { type: String, required: true },
  titleId: { type: String, default: undefined },
  subtitle: { type: String, default: '' },
  integrationId: { type: String, default: null },
  customMenu: Boolean,
  refreshable: Boolean,
  refreshing: Boolean,
  refreshDisabled: Boolean,
  refreshLabel: { type: String, default: 'Aktualisieren' },
  canMoveLeft: Boolean,
  canMoveRight: Boolean
})
const emit = defineEmits(['refresh', 'close', 'remove', 'move'])
const hasMenu = computed(() => Boolean(props.integrationId || props.customMenu))
</script>
