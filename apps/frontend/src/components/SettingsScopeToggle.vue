<template>
  <q-btn-toggle
    v-if="isAdmin"
    :model-value="modelValue"
    class="settings-scope-toggle"
    unelevated
    no-caps
    color="grey-7"
    text-color="white"
    toggle-color="primary"
    :options="options"
    :disable="disable"
    aria-label="Einstellungsbereich"
    @update:model-value="$emit('update:modelValue', $event)"
  />
</template>

<script setup>
defineOptions({ name: 'SettingsScopeToggle' })

defineProps({
  modelValue: { type: String, required: true },
  isAdmin: { type: Boolean, required: true },
  disable: { type: Boolean, default: false }
})
defineEmits(['update:modelValue'])

const options = [
  { label: 'Persönlich', value: 'personal' },
  { label: 'Instanz', value: 'instance' }
]
</script>
