<template>
  <span class="session-status-indicator row items-center justify-center" role="status" :aria-label="status.label">
    <template v-if="status.kind === 'approval'">
      <q-icon name="pending_actions" color="warning" size="16px" />
      <q-badge v-if="status.count > 1" floating rounded color="warning" text-color="dark" :label="status.count" />
    </template>
    <q-spinner-grid v-else-if="status.kind === 'active'" color="primary" size="16px" />
    <q-icon v-else name="fiber_manual_record" color="primary" size="12px" />
    <slot />
  </span>
</template>

<script setup>
defineProps({ status: { type: Object, required: true } })
</script>

<style scoped>
.session-status-indicator {
  position: relative;
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
}

.session-status-indicator :deep(.q-badge--floating) {
  top: -5px;
  right: -6px;
  min-width: 16px;
  min-height: 16px;
  padding: 1px 4px;
  font-size: 0.625rem;
  line-height: 1;
}
</style>
