<template>
  <q-item clickable dense v-ripple class="sidebar-row items-center rounded-borders q-px-sm q-py-xs" active-class="sidebar-row--active">
    <slot name="icon">
      <q-item-section v-if="icon" side class="q-pr-sm"><q-icon :name="icon" :color="iconColor" size="22px" /></q-item-section>
    </slot>
    <q-item-section>
      <q-item-label :id="labelId" lines="1" class="text-body2">{{ label }}<slot name="label-suffix" /></q-item-label>
    </q-item-section>
    <q-item-section v-if="$slots.actions" side class="q-pl-xs"><slot name="actions" /></q-item-section>
    <q-item-section v-if="expanded !== null" side class="q-pl-xs">
      <q-icon :name="expanded ? 'expand_more' : 'chevron_right'" size="22px" />
    </q-item-section>
    <slot />
  </q-item>
</template>

<script setup>
defineProps({
  label: { type: String, required: true },
  icon: { type: String, default: '' },
  iconColor: { type: String, default: undefined },
  labelId: { type: String, default: undefined },
  expanded: { type: Boolean, default: null }
})
</script>
