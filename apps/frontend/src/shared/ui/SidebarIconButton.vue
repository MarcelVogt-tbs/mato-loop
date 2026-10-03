<template>
  <q-btn flat round dense size="sm" class="sidebar-icon-button" :aria-label="ariaLabel" @click.stop>
    <q-icon :name="icon" size="22px" />
    <slot />
  </q-btn>
</template>

<script setup>
defineProps({
  icon: { type: String, required: true },
  ariaLabel: { type: String, required: true }
})
</script>
