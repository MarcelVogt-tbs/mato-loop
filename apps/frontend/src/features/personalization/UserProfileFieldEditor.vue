<template>
  <div>
    <div class="row items-start no-wrap q-gutter-x-sm">
      <q-input dense
        class="col"
        :model-value="modelValue"
        outlined
        hide-bottom-space
        clearable
        maxlength="20000"
        :label="field.label"
        :hint="hint"
        :disable="inputDisabled"
        @update:model-value="$emit('update:modelValue', $event)"
      />
      <q-btn
        flat
        round
        dense
        class="q-mt-xs"
        color="negative"
        icon="delete_outline"
        :aria-label="`${field.label} entfernen`"
        :disable="disabled"
        @click="$emit('remove')"
      >
        <q-tooltip>Angabe entfernen</q-tooltip>
      </q-btn>
    </div>
    <q-checkbox dense
      v-if="audience === 'private' && modelTrustEnabled"
      :model-value="shared"
      class="q-mt-sm"
      color="primary"
      label="Auch für öffentliche Modelle verwenden"
      :disable="disabled"
      @update:model-value="$emit('update:shared', $event)"
    />
  </div>
</template>

<script setup>
defineOptions({ name: 'UserProfileFieldEditor' })

defineProps({
  field: { type: Object, required: true },
  modelValue: { type: String, default: null },
  shared: Boolean,
  audience: { type: String, required: true },
  modelTrustEnabled: { type: Boolean, default: true },
  hint: { type: String, required: true },
  inputDisabled: Boolean,
  disabled: Boolean
})

defineEmits(['update:modelValue', 'update:shared', 'remove'])
</script>
