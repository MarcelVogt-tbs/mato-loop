<template>
  <q-banner v-if="failed" rounded class="app-surface-tonal">Die Integrationsvorgabe konnte nicht geladen werden.<template #action><q-btn flat label="Erneut laden" @click="load" /></template></q-banner>
  <AppSelect v-else :model-value="value?.integrationsEnabledByDefault ?? null" outlined emit-value map-options label="Integrationen standardmäßig verfügbar" :options="[{ label: `Vorgabe: ${value?.inheritedIntegrationsEnabledByDefault ? 'Verfügbar' : 'Nicht verfügbar'}`, value: null }, { label: 'Verfügbar', value: true }, { label: 'Nicht verfügbar', value: false }]" :disable="busy || !value" @update:model-value="save" />
</template>

<script setup>
import { ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { useIntegrationStore } from 'stores/integrations'

const props = defineProps({ profileId: { type: String, required: true }, scope: { type: String, required: true } })
const integrations = useIntegrationStore()
const $q = useQuasar()
const value = ref(null)
const busy = ref(false)
const failed = ref(false)
let revision = 0
async function load() {
  const token = ++revision
  busy.value = true
  failed.value = false
  value.value = null
  try {
    const data = await integrations.fetchProfileAvailability(props.profileId, props.scope)
    if (token === revision) value.value = data
  } catch { if (token === revision) failed.value = true }
  finally { if (token === revision) busy.value = false }
}
async function save(enabled) {
  const token = ++revision
  busy.value = true
  try {
    const data = await integrations.saveProfileAvailability(props.profileId, props.scope, { integrationsEnabledByDefault: enabled })
    if (token === revision) value.value = data
    $q.notify({ type: 'positive', message: 'Integrationsvorgabe gespeichert' })
  } catch { /* Central API toast. */ }
  finally { if (token === revision) busy.value = false }
}
watch([() => props.profileId, () => props.scope], load, { immediate: true })
</script>
