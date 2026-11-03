<template>
  <q-card flat bordered tag="article" class="composer-item-card app-surface-tonal row no-wrap items-center overflow-hidden relative-position">
    <slot />
    <div v-if="removable || $slots.actions" class="composer-item-card__actions absolute row no-wrap">
      <slot name="actions" />
      <q-btn
        v-if="removable"
        flat round dense icon="close" size="sm" type="button"
        :aria-label="removeLabel" :disable="disabled"
        @click="$emit('remove')"
      />
    </div>
  </q-card>
</template>

<script setup>
defineProps({
  disabled: Boolean,
  removable: { type: Boolean, default: true },
  removeLabel: { type: String, default: 'Anhang entfernen' }
})
defineEmits(['remove'])
</script>
