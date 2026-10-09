<template>
  <img
    class="app-logo"
    :src="appearance.logoUrl"
    alt=""
    aria-hidden="true"
    :width="size"
    :height="size"
  />
</template>

<script setup>
import { useAppearanceStore } from 'src/features/settings/appearance-store'

const appearance = useAppearanceStore()
defineProps({
  size: {
    type: Number,
    default: 36
  }
})
</script>

<style scoped>
.app-logo {
  display: inline-block;
  flex: none;
  vertical-align: middle;
}
</style>
