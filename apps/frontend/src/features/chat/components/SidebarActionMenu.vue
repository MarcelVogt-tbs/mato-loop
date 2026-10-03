<template>
  <SidebarIconButton
    icon="more_horiz"
    :color="color"
    :aria-label="ariaLabel"
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
  </SidebarIconButton>
</template>

<script setup>
import SidebarIconButton from 'src/shared/ui/SidebarIconButton.vue'
defineOptions({ name: 'SidebarActionMenu' })

defineProps({
  ariaLabel: { type: String, required: true },
  color: { type: String, default: undefined },
  listClass: { type: String, default: undefined }
})

const open = defineModel({ type: Boolean, required: true })
</script>
