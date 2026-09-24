<template>
  <img
    class="app-logo"
    src="/icons/matoloop.svg"
    alt=""
    aria-hidden="true"
    :width="size"
    :height="size"
  />
</template>

<script setup>
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
