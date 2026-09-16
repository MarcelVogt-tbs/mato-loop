<template>
  <q-btn
    flat
    round
    dense
    size="sm"
    icon="more_horiz"
    :color="color"
    :aria-label="ariaLabel"
    @click.stop
  >
    <q-menu
      v-model="open"
      anchor="bottom right"
      self="top right"
      :offset="[0, 8]"
      class="app-action-menu"
    >
      <q-list padding :class="listClass">
        <slot />
      </q-list>
    </q-menu>
  </q-btn>
</template>

<script setup>
defineOptions({ name: 'SidebarActionMenu' })

defineProps({
  ariaLabel: { type: String, required: true },
  color: { type: String, default: undefined },
  listClass: { type: String, default: undefined }
})

const open = defineModel({ type: Boolean, required: true })
</script>
