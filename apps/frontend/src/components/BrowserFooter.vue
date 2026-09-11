<template>
  <div class="q-px-sm q-py-xs">
    <div class="row items-center q-gutter-xs">
      <div class="col-12 col-sm text-caption app-muted" role="status" aria-live="polite">
        {{ runtimeStatus }}
      </div>
      <q-toggle :model-value="browser.ownsDisplay && browser.attached" dense label="Agent verbinden"
        :disable="!browser.ready || browser.transferring || browser.navigationPending || Boolean(browser.submitting) || (!browser.attached && (browser.busy || !browser.canAttach))"
        @update:model-value="browser.setAttachment($event)" />
      <q-btn v-if="browser.ownsDisplay && (browser.attached || requestedControl)" flat dense no-caps label="Manuell übernehmen" icon="pan_tool"
        :disable="!browser.ready || (!requestedControl && (browser.transferring || browser.navigationPending || Boolean(browser.submitting)))" @click="browser.setAttachment(false)" />
    </div>
    <div v-if="browser.ready && !browser.busy && !browser.canAttach && !browser.attached" class="text-caption app-muted" role="status">{{ attachReason }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useBrowserStore } from 'stores/browser'
import { apiErrorMessage } from 'src/utils/api-errors'

const browser = useBrowserStore()
const requestedControl = computed(() => browser.ownsDisplay && Boolean(browser.controlRequestId))
const runtimeStatus = computed(() => {
  if (browser.displayStatus === 'occupied') return 'Browser in einer anderen Ansicht belegt. Übernahme im Browser ausdrücklich anfordern.'
  if (!browser.ready) return 'Browser nicht verbunden'
  if (requestedControl.value) return 'Agent-Verbindung angefordert. Du kannst die Anfrage durch manuelle Übernahme abbrechen.'
  if (browser.navigationPending) return 'Browsernavigation wird ausgeführt.'
  if (browser.submitting === 'detach') return 'Übernahme angefordert. Manuelle Eingaben bleiben bis zur Freigabe gesperrt.'
  if (browser.busy && !browser.attached) return 'Agent getrennt. Die laufende Aktion wird noch beendet; manuelle Eingaben bleiben bis dahin gesperrt.'
  if (browser.busy) return 'Der Browser führt eine Aktion aus. Du kannst die manuelle Übernahme anfordern.'
  return browser.attached ? 'Agent verbunden. Manuelle Eingaben sind gesperrt.' : 'Manuelle Steuerung'
})
const attachReason = computed(() => apiErrorMessage({ error: { code: browser.attachReason } },
  'Die Browser-Integration ist für das aktuelle Agentenprofil nicht freigegeben. Prüfe die Integrationsauswahl im Agentenprofil.'))
</script>
