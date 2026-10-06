<template>
  <q-menu v-bind="$attrs" class="app-menu-popup app-action-menu" :offset="offset">
    <slot />
  </q-menu>
</template>

<script setup>
defineOptions({ name: 'AppMenu', inheritAttrs: false })
defineProps({ offset: { type: Array, default: () => [0, 8] } })
</script>
