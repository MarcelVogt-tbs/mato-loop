<template>
  <div class="app-state-message column no-wrap items-center text-center scroll">
    <div class="full-width col-auto q-my-auto">
      <q-icon v-if="icon" :name="icon" size="48px" :color="iconColor || undefined" :class="{ 'app-muted': !iconColor }" aria-hidden="true" />
      <div v-if="title" class="text-subtitle1 text-weight-medium" :class="{ 'q-mt-md': icon }">{{ title }}</div>
      <div v-if="description" class="text-body2 app-muted" :class="{ 'q-mt-xs': title, 'q-mt-md': icon && !title }">{{ description }}</div>
      <div v-if="$slots.actions" class="q-mt-md">
        <div class="row items-center justify-center q-gutter-sm">
          <slot name="actions" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'AppStateMessage' })

defineProps({
  icon: { type: String, default: '' },
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  iconColor: { type: String, default: '' }
})
</script>

<style scoped>
.app-state-message {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
