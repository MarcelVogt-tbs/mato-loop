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
          :rules="[positiveIntegerRule, contextRule]"
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
      <q-input
        v-model.number="form.maxOutputTokens"
        outlined
        hide-bottom-space
        type="number"
        min="1"
        step="1"
        label="Maximale Ausgabe (Tokens)"
        hint="Muss kleiner als das Kontextfenster sein."
        lazy-rules
        :rules="[positiveIntegerRule, outputRule]"
      />
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
        <q-toggle
          v-model="form.supportsImageInput"
          color="primary"
          label="Bildeingaben unterstützt"
        />
        <div class="model-runtime-wide">
          <q-select
            v-model="form.webfetchPolicy"
            outlined
            hide-bottom-space
            emit-value
            map-options
            label="Webfetch-Override"
            :options="WEBFETCH_POLICY_OPTIONS"
            hint="Überschreibt die globale Standardpolicy nur für dieses Modell."
          />
          <div class="row items-center q-gutter-sm q-mt-sm">
            <span class="text-caption app-muted">Effektiv:</span>
            <q-chip dense square :color="effectivePolicyColor" text-color="white" icon="policy">
              {{ form.supportsTools ? webfetchPolicyLabel(effectiveWebfetchPolicy) : 'Nicht verfügbar' }}
            </q-chip>
            <span v-if="!form.supportsTools && effectiveWebfetchPolicy !== 'disabled'" class="text-caption text-warning">
              Ohne Tool-Unterstützung kann Webfetch nicht angeboten werden.
            </span>
          </div>
        </div>
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
import { WEBFETCH_POLICY_OPTIONS, webfetchPolicyLabel } from 'src/utils/webfetch'

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
const props = defineProps({
  reasoningSummaryAvailable: Boolean,
  reasoningSummaryForced: Boolean,
  effectiveWebfetchPolicy: { type: String, default: null },
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

const effectivePolicyColor = computed(() => ({
  disabled: 'grey-7',
  ask: 'warning',
  auto_public: 'primary',
  auto_allowed: 'positive'
})[form.value.supportsTools ? props.effectiveWebfetchPolicy : 'disabled'] || 'grey-7')

function positiveIntegerRule(value) {
  return Number.isInteger(Number(value)) && Number(value) > 0 || 'Bitte gib eine positive ganze Zahl ein.'
}

function positiveNumberRule(value) {
  return Number.isFinite(Number(value)) && Number(value) > 0 || 'Bitte gib eine positive Zahl ein.'
}

function contextRule(value) {
  return Number(value) > Number(form.value.maxOutputTokens) || 'Das Kontextfenster muss größer als die maximale Ausgabe sein.'
}

function outputRule(value) {
  return Number(value) < Number(form.value.contextWindowTokens) || 'Die maximale Ausgabe muss kleiner als das Kontextfenster sein.'
}

function toggleProviderContextWindow() {
  if (form.value.useDiscoveredContextWindow) {
    form.value.useDiscoveredContextWindow = false
    return
  }
  form.value.useDiscoveredContextWindow = true
  form.value.contextWindowTokens = form.value.discoveredContextWindowTokens
  if (Number(form.value.maxOutputTokens) >= Number(form.value.contextWindowTokens)) {
    form.value.maxOutputTokens = Math.max(1, Math.floor(form.value.contextWindowTokens / 4))
  }
}
</script>
