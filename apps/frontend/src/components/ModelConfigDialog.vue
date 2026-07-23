<template>
  <q-dialog :model-value="modelValue" @hide="close">
    <q-card class="provider-dialog model-dialog app-surface">
      <q-card-section class="row items-start no-wrap q-gutter-md">
        <q-avatar color="primary" text-color="white" icon="smart_toy" />
        <div class="col">
          <div class="text-h6">{{ title }}</div>
          <div class="text-body2 app-muted">
            {{ model ? 'Anzeigename, Status und Laufzeit bearbeiten.' : `Manuelles Modell für ${provider?.name || 'den Provider'} hinzufügen.` }}
          </div>
        </div>
        <q-btn flat round dense icon="close" aria-label="Dialog schließen" @click="close" />
      </q-card-section>

      <q-separator />

      <q-card-section class="provider-dialog__body scroll">
        <q-form ref="formRef" class="provider-form-grid">
          <q-input
            v-model="form.modelId"
            outlined
            label="Modell-ID"
            lazy-rules
            :readonly="isDiscovered"
            :rules="[requiredRule]"
            :hint="isDiscovered ? 'Die ID eines entdeckten Modells wird vom Provider vorgegeben.' : ''"
          />
          <q-input
            v-model="form.name"
            outlined
            label="Anzeigename (optional)"
            hint="Ohne Anzeigename wird die Modell-ID verwendet."
          />
          <q-toggle
            v-model="form.enabled"
            class="provider-form-section"
            color="primary"
            label="Modell aktiv"
            aria-label="Modell aktiv"
            :disable="model?.availability === 'missing'"
          />
          <ModelRuntimeFields
            :model-value="form"
            :reasoning-summary-available="reasoningSummaryAvailable"
            :reasoning-summary-forced="provider?.adapterType === 'openai-codex'"
            :effective-webfetch-policy="effectiveWebfetchPolicy"
            :provider-trust-level="provider?.trustLevel"
            :provider-pseudonymization-enabled="provider?.pseudonymizationEnabled"
          />
        </q-form>
      </q-card-section>

      <q-separator />
      <q-card-actions class="q-pa-md">
        <q-btn flat no-caps label="Abbrechen" @click="close" />
        <q-space />
        <q-btn
          unelevated
          no-caps
          color="primary"
          :label="model ? 'Änderungen speichern' : 'Modell hinzufügen'"
          :loading="saving"
          @click="save"
        />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import ModelRuntimeFields from 'components/ModelRuntimeFields.vue'
import { modelRuntimeForm, modelRuntimePayload } from 'src/utils/model-runtime'
import { resolveWebfetchPolicy } from 'src/utils/webfetch'
import { useProviderStore } from 'stores/providers'
import { useToolSettingsStore } from 'stores/tool-settings'

defineOptions({ name: 'ModelConfigDialog' })

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  provider: { type: Object, default: null },
  model: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue', 'saved'])
const $q = useQuasar()
const providers = useProviderStore()
const tools = useToolSettingsStore()
const formRef = ref(null)
const saving = ref(false)
const form = reactive(defaultForm())
const isDiscovered = computed(() => props.model?.origin === 'discovered')
const title = computed(() => props.model ? `${props.model.name || props.model.modelId} bearbeiten` : 'Modell hinzufügen')
const reasoningSummaryAvailable = computed(() => props.provider?.adapterType === 'openai-codex'
  || ['responses'].includes(props.provider?.config?.apiType || props.provider?.config?.api_type))
const effectiveWebfetchPolicy = computed(() => resolveWebfetchPolicy(
  form.webfetchPolicy,
  tools.loaded ? tools.webFetch : null,
  form.effectiveWebfetchPolicy
))

function defaultForm() {
  return {
    modelId: '',
    name: '',
    enabled: true,
    ...modelRuntimeForm()
  }
}

function initialize() {
  Object.assign(form, defaultForm(), props.model ? {
    modelId: props.model.modelId,
    name: props.model.name === props.model.modelId ? '' : props.model.name || '',
    enabled: props.model.enabled,
    ...modelRuntimeForm(props.model)
  } : {})
}

function requiredRule(value) {
  return Boolean(String(value || '').trim()) || 'Pflichtfeld'
}

async function save() {
  if (!await formRef.value?.validate() || !props.provider) return
  saving.value = true
  try {
    const payload = {
      model_id: form.modelId.trim(),
      name: form.name.trim() || null,
      enabled: form.enabled,
      ...modelRuntimePayload(form)
    }
    if (props.model) await providers.updateModel(props.model.id, payload)
    else await providers.createModel(props.provider.id, payload)
    $q.notify({ type: 'positive', message: props.model ? 'Modell gespeichert' : 'Modell hinzugefügt' })
    emit('saved')
    close()
  } finally {
    saving.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}

watch(() => props.modelValue, (open) => {
  if (open) {
    initialize()
    if (!tools.loaded) tools.fetchSettings().catch(() => {})
  }
})
</script>
