<template>
  <div
    v-if="request.payload?.subagentSessionId"
    class="request-progress__source col-12 col-sm-auto order-last row items-center justify-end no-wrap text-caption q-ml-auto"
  >
    <svg class="col-auto app-muted q-mr-xs" width="18" height="18" viewBox="0 0 18 18" fill="none" focusable="false" aria-hidden="true">
      <rect x="4.75" y="4.75" width="8.5" height="8.5" rx="1.5" stroke="currentColor" stroke-width="1.5" />
    </svg>
    <router-link :to="{ name: 'chat', query: { ...(route.query.session === request.sessionId ? route.query : {}), session: request.sessionId, subagent: request.payload.subagentSessionId } }" class="text-primary text-right">
      {{ request.payload.subagentTitle || 'Subagent' }}
    </router-link>
  </div>
</template>

<script setup>
import { useRoute } from 'vue-router'
const route = useRoute()
defineProps({ request: { type: Object, required: true } })
</script>
