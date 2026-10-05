<template>
  <q-card flat class="app-surface q-mb-md">
    <q-card-section>
      <div class="row items-center q-col-gutter-md">
        <div class="col-12 col-sm row items-center no-wrap q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="person" />
          <div>
            <div class="text-subtitle2 text-weight-bold">{{ title }}</div>
            <div class="text-caption app-muted">{{ description }}</div>
            <div class="text-caption app-muted">Gilt für reguläre Benutzer. Administratoren können persönliche Einstellungen weiterhin verwenden.</div>
          </div>
        </div>
        <q-toggle dense :model-value="modelValue" class="col-12 col-sm-auto" color="primary" :aria-label="title" :disable="disabled" @update:model-value="$emit('update:modelValue', $event)" />
      </div>
    </q-card-section>
  </q-card>
</template>

<script setup>
defineProps({ modelValue: Boolean, title: { type: String, required: true }, description: { type: String, required: true }, disabled: Boolean })
defineEmits(['update:modelValue'])
</script>
