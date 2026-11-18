<template>
  <q-dialog :model-value="modelValue" :transition-duration="0" :maximized="$q.screen.lt.sm" @update:model-value="$emit('update:modelValue', $event)">
    <q-card class="integration-dialog app-surface column no-wrap">
      <q-card-section class="row items-center">
        <div class="text-h6 col">Integration hinzufügen</div>
        <q-btn v-close-popup flat round dense icon="close" aria-label="Dialog schließen" />
      </q-card-section>
      <q-form @submit.prevent="proceed">
        <q-card-section class="q-gutter-md">
          <AppSelect v-model="type" outlined emit-value map-options label="Integrationstyp" :options="types" :rules="[value => Boolean(value) || 'Integrationstyp auswählen.']" @update:model-value="selectType" />
          <AppSelect v-model="provider" outlined emit-value map-options label="Anbieter" :options="providers.map(item => ({ label: item.label, value: item.provider }))" @update:model-value="name = providers.find(item => item.provider === $event)?.label || ''" />
          <q-input v-model.trim="name" dense outlined label="Name" maxlength="160" :rules="[value => Boolean(value?.trim()) || 'Name eingeben.']" />
          <div class="text-caption app-muted">Die Verbindung wird auf der nächsten Seite eingerichtet. Erst „Speichern“ legt die Integration an.</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn v-close-popup flat no-caps label="Abbrechen" />
          <q-btn unelevated no-caps color="primary" label="Weiter" type="submit" :disable="!types.length" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { useIntegrationStore } from 'stores/integrations'
import { integrationTypes, providersForType } from './configuration'

const props = defineProps({ modelValue: Boolean, scope: { type: String, required: true } })
const emit = defineEmits(['update:modelValue', 'continue'])
const $q = useQuasar()
const integrations = useIntegrationStore()
const types = computed(() => integrationTypes.filter((item) => providersForType(item.value).some(provider => integrations.adapterAvailable(provider.provider, props.scope))))
const type = ref(null)
const provider = ref(null)
const providers = computed(() => providersForType(type.value).filter(item => integrations.adapterAvailable(item.provider, props.scope)))
const name = ref('')
function selectType(value) {
  type.value = value
  provider.value = providers.value[0]?.provider || null
  name.value = providers.value[0]?.label || ''
}
watch(() => props.modelValue, (open) => {
  if (!open) return
  selectType(types.value[0]?.value || null)
}, { immediate: true })
function proceed() {
  if (!types.value.some((item) => item.value === type.value) || !providers.value.some(item => item.provider === provider.value) || !name.value.trim()) return
  emit('continue', { type: type.value, provider: provider.value, name: name.value.trim() })
  emit('update:modelValue', false)
}
</script>
