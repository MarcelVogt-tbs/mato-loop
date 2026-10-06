<template>
  <SidebarIconButton
    icon="more_horiz"
    :color="color"
    :aria-label="ariaLabel"
  >
    <AppMenu
      v-model="open"
      anchor="bottom right"
      self="top right"
    >
      <q-list padding :class="listClass">
        <slot />
      </q-list>
    </AppMenu>
  </SidebarIconButton>
</template>

<script setup>
import AppMenu from 'src/shared/ui/AppMenu.vue'
import SidebarIconButton from 'src/shared/ui/SidebarIconButton.vue'
defineOptions({ name: 'SidebarActionMenu' })

defineProps({
  ariaLabel: { type: String, required: true },
  color: { type: String, default: undefined },
  listClass: { type: String, default: undefined }
})

const open = defineModel({ type: Boolean, required: true })
</script>
