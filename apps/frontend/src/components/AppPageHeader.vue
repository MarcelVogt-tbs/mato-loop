<template>
  <header class="app-page-header">
    <div>
      <h1 class="text-h5 text-weight-bold q-my-none">{{ title }}</h1>
      <p v-if="subtitle" class="text-body2 app-muted q-mb-none q-mt-xs">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="row items-center q-gutter-sm">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup>
defineOptions({ name: 'AppPageHeader' })

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' }
})
</script>
