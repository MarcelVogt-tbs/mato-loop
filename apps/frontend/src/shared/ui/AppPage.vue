<template>
  <q-page class="app-page">
    <AppPageHeader :title="title" :subtitle="subtitle">
      <template v-if="$slots.actions" #actions>
        <slot name="actions" />
      </template>
    </AppPageHeader>
    <slot />
  </q-page>
</template>

<script setup>
import AppPageHeader from './AppPageHeader.vue'

defineOptions({ name: 'AppPage' })

defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' }
})
</script>
