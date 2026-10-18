<template>
  <div
    v-if="chat.current"
    class="control-center__resources row items-center no-wrap overflow-hidden q-px-md q-py-xs text-caption app-muted"
  >
    <div class="control-center__resource row items-center no-wrap q-mr-md" tabindex="0" :aria-label="requestDescription">
      <q-icon name="forum" size="18px" class="q-mr-xs" />
      <span class="ellipsis">{{ requestLabel }}</span>
      <q-tooltip>{{ requestDescription }}</q-tooltip>
    </div>
    <div
      v-if="contextPercentage !== null"
      class="control-center__resource row items-center no-wrap"
      tabindex="0"
      :aria-label="contextDescription"
    >
      <q-icon name="token" size="18px" class="q-mr-xs" />
      <q-linear-progress
        :value="contextProgress"
        size="4px"
        rounded
        class="control-center__resource-progress q-mr-xs"
        aria-hidden="true"
      />
      <span class="ellipsis">≈ {{ contextPercentage }} %</span>
      <q-tooltip>{{ contextDescription }}</q-tooltip>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useChatStore } from 'src/features/chat/store'
import { formatMetricInteger, modelContextPercentage } from 'src/utils/resource-metrics'

defineOptions({ name: 'SessionFooter' })

const chat = useChatStore()
const requestCount = computed(() => {
  const count = chat.current?.userRequestCount
  return Number.isInteger(count) && count >= 0 ? count : null
})
const requestLabel = computed(() => formatMetricInteger(requestCount.value) ?? '–')
const requestDescription = computed(() => requestCount.value === null
  ? 'Anfragezahl nicht verfügbar.'
  : `${formatMetricInteger(requestCount.value)} Nutzeranfragen in dieser Session.`)
const contextPercentage = computed(() => modelContextPercentage(chat.current?.latestModelContext))
const contextProgress = computed(() => Math.min((contextPercentage.value || 0) / 100, 1))
const contextDescription = computed(() => {
  const context = chat.current?.latestModelContext
  return `Letzter Modellaufruf: ≈ ${contextPercentage.value} % (${formatMetricInteger(context.estimatedInputTokens)} / ${formatMetricInteger(context.inputBudgetTokens)} Tokens).`
})
</script>
