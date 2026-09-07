<template>
  <q-expansion-item
    icon="tune"
    label="Erweiterte Laufzeit"
    caption="Kontext, Ausgabelänge und Modellfähigkeiten"
    class="provider-form-section model-runtime-expansion"
  >
    <q-card-section class="model-runtime-grid q-px-none q-pb-none">
      <div class="model-runtime-context-field">
        <q-input
          v-model.number="form.contextWindowTokens"
          outlined
          hide-bottom-space
          type="number"
          min="2"
          step="1"
          label="Kontextfenster (Tokens)"
          :hint="contextWindowHint"
          :disable="form.useDiscoveredContextWindow && form.discoveredContextWindowTokens != null"
          lazy-rules
          :rules="[positiveIntegerRule, value => Number(value) >= 2 || 'Das Kontextfenster muss mindestens 2 Tokens umfassen.']"
        />
        <q-btn
          v-if="form.discoveredContextWindowTokens != null"
          flat
          no-caps
          color="primary"
          :label="form.useDiscoveredContextWindow ? 'Manuell festlegen' : 'Providerwert verwenden'"
          @click="toggleProviderContextWindow"
        />
      </div>
      <div>
        <q-toggle
          :model-value="form.maxOutputTokens == null"
          color="primary"
          label="Ausgabelimit: Auto"
          @update:model-value="form.maxOutputTokens = $event ? null : (form.discoveredMaxOutputTokens ?? form.contextWindowTokens)"
        />
        <div v-if="generationOptionsOmitted" class="text-caption app-muted q-mb-sm">
          Bei ChatGPT Codex wird kein Ausgabelimit übertragen. Die Einstellung beeinflusst nur die interne Kontextplanung; die Ausgabe begrenzt der Provider.
        </div>
        <div v-else class="text-caption app-muted q-mb-sm">
          Auto passt das Ausgabebudget an den freien Kontext an. Ein manuelles Limit ist eine zusätzliche Obergrenze, keine reservierte Ausgabelänge.
        </div>
        <q-input
          v-if="form.maxOutputTokens != null"
          v-model.number="form.maxOutputTokens"
          outlined
          hide-bottom-space
          type="number"
          min="1"
          step="1"
          label="Manuelles Ausgabelimit (Tokens)"
          :hint="generationOptionsOmitted
            ? 'Darf größer als das Kontextfenster sein; dient nur der internen Kontextplanung.'
            : 'Darf größer als das Kontextfenster sein; das Budget wird pro Anfrage begrenzt.'"
          lazy-rules
          :rules="[positiveIntegerRule]"
        />
        <div v-if="!generationOptionsOmitted" class="text-caption app-muted q-mt-sm">{{ outputLimitHint }}</div>
      </div>
      <q-input
        v-model.number="form.charactersPerToken"
        outlined
        hide-bottom-space
        type="number"
        min="0.1"
        step="0.1"
        label="Zeichen pro Token"
        hint="Konservativer Schätzwert für unbekannte Tokenizer, meist etwa 4."
        lazy-rules
        :rules="[positiveNumberRule]"
      />
      <q-select
        :model-value="form.trustLevel ?? 'inherit'"
        outlined
        hide-bottom-space
        emit-value
        map-options
        label="Vertrauenslevel"
        :options="trustLevelOptions"
        :hint="`Effektiv: ${effectiveTrustLevel === 'private' ? 'Privat' : 'Öffentlich'}`"
        @update:model-value="form.trustLevel = $event === 'inherit' ? null : $event"
      />
      <q-select
        :model-value="form.pseudonymizationEnabled ?? 'inherit'"
        outlined
        hide-bottom-space
        emit-value
        map-options
        label="Schutz von Kennungen und Secrets"
        :options="pseudonymizationOptions"
        :hint="`Effektiv: ${effectivePseudonymizationEnabled ? 'Aktiv' : 'Inaktiv'}. Personen und Orte werden nicht erkannt.`"
        @update:model-value="form.pseudonymizationEnabled = $event === 'inherit' ? null : $event"
      />
      <q-select
        v-model="form.memoryPolicy"
        outlined
        hide-bottom-space
        emit-value
        map-options
        label="Memory-Policy"
        :options="memoryPolicyOptions"
        hint="Kann Memory für dieses Modell explizit erlauben oder sperren."
      />
      <div class="model-runtime-toggles">
        <q-toggle
          v-model="form.supportsTools"
          color="primary"
          label="Tools unterstützt"
        />
        <q-toggle
          v-model="form.supportsParallelToolCalls"
          color="primary"
          label="Parallele Tool-Aufrufe"
          :disable="!form.supportsTools"
        />
        <q-toggle
          v-model="form.supportsStreaming"
          color="primary"
          label="Streaming unterstützt"
        />
        <q-select
          v-model="form.inputModalities"
          class="model-runtime-wide"
          outlined
          hide-bottom-space
          multiple
          use-chips
          emit-value
          map-options
          label="Eingabemodalitäten"
          :options="inputModalityOptions"
          hint="Eingabearten, die das Modell nativ verarbeiten kann."
          @update:model-value="form.supportsImageInput = $event.includes('image')"
        />
        <div v-if="reasoningSummaryAvailable" class="model-runtime-wide">
          <q-toggle
            :model-value="reasoningSummaryForced || form.reasoningSummary"
            color="primary"
            :disable="reasoningSummaryForced"
            :label="reasoningSummaryForced ? 'Reasoning-Zusammenfassung aktiv' : 'Reasoning-Zusammenfassung anfordern'"
            @update:model-value="form.reasoningSummary = $event"
          />
          <div class="text-caption app-muted q-ml-sm">
            {{ reasoningSummaryForced
              ? 'Codex stellt öffentliche Reasoning-Summaries bereit.'
              : 'Nur aktivieren, wenn das Responses-Modell öffentliche Summaries unterstützt.' }}
          </div>
        </div>
      </div>
    </q-card-section>
  </q-expansion-item>
</template>

<script setup>
import { computed } from 'vue'

defineOptions({ name: 'ModelRuntimeFields' })

const form = defineModel({ type: Object, required: true })
const trustLevelOptions = [
  { label: 'Provider übernehmen', value: 'inherit' },
  { label: 'Öffentlich', value: 'public' },
  { label: 'Privat', value: 'private' }
]
const pseudonymizationOptions = [
  { label: 'Provider übernehmen', value: 'inherit' },
  { label: 'Aktiv', value: true },
  { label: 'Inaktiv', value: false }
]
const memoryPolicyOptions = [
  { label: 'Provider-Einstellung übernehmen', value: 'inherit' },
  { label: 'Erlauben', value: 'allow' },
  { label: 'Sperren', value: 'deny' }
]
const inputModalityOptions = [
  { label: 'Text', value: 'text' },
  { label: 'Bild', value: 'image' },
  { label: 'Audio', value: 'audio' },
  { label: 'Video', value: 'video' }
]
const props = defineProps({
  generationOptionsOmitted: { type: Boolean, default: false },
  reasoningSummaryAvailable: Boolean,
  reasoningSummaryForced: Boolean,
  providerTrustLevel: { type: String, default: 'public' },
  providerPseudonymizationEnabled: { type: Boolean, default: true }
})

const effectiveTrustLevel = computed(() => form.value.trustLevel ?? props.providerTrustLevel)
const effectivePseudonymizationEnabled = computed(() => (
  effectiveTrustLevel.value === 'public'
  && (form.value.pseudonymizationEnabled ?? props.providerPseudonymizationEnabled)
))
const contextWindowHint = computed(() => {
  if (form.value.discoveredContextWindowTokens == null) {
    return 'Der Provider meldet kein Kontextfenster; der konfigurierte Wert wird verwendet.'
  }
  if (form.value.useDiscoveredContextWindow) {
    return `Automatisch vom Provider erkannt: ${form.value.discoveredContextWindowTokens.toLocaleString('de-DE')} Tokens.`
  }
  return `Manueller Override; Providerwert: ${form.value.discoveredContextWindowTokens.toLocaleString('de-DE')} Tokens.`
})
const outputLimitHint = computed(() => form.value.discoveredMaxOutputTokens == null
  ? 'Provider-Ausgabelimit unbekannt. Der Provider kann die Ausgabe zusätzlich begrenzen.'
  : `Gemeldetes Provider-Ausgabelimit: ${form.value.discoveredMaxOutputTokens.toLocaleString('de-DE')} Tokens.`)

function positiveIntegerRule(value) {
  return Number.isInteger(Number(value)) && Number(value) > 0 || 'Bitte gib eine positive ganze Zahl ein.'
}

function positiveNumberRule(value) {
  return Number.isFinite(Number(value)) && Number(value) > 0 || 'Bitte gib eine positive Zahl ein.'
}

function toggleProviderContextWindow() {
  if (form.value.useDiscoveredContextWindow) {
    form.value.useDiscoveredContextWindow = false
    return
  }
  form.value.useDiscoveredContextWindow = true
  form.value.contextWindowTokens = form.value.discoveredContextWindowTokens
}
</script>
