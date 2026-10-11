<template>
  <AppSelect dense v-if="allowInheritance" v-model="form.shellCustom" outlined emit-value map-options label="Shell-Präfixe" :options="[{ label: 'Vom Basisprofil übernehmen', value: false }, { label: 'Eigene Werte', value: true }]" :disable="disabled" />
  <div class="row q-col-gutter-md q-mt-sm">
    <q-input dense v-model="form.shellRead" class="col-12 col-sm-6" outlined type="textarea" autogrow label="Lesende Befehle" hint="Ein Präfix pro Zeile, z. B. git status." :disable="disabled || !form.shellCustom" />
    <q-input dense v-model="form.shellWrite" class="col-12 col-sm-6" outlined type="textarea" autogrow label="Schreibende Befehle" hint="Ein Präfix pro Zeile, z. B. npm install." :disable="disabled || !form.shellCustom" />
  </div>
</template>

<script setup>
import AppSelect from 'src/shared/ui/AppSelect.vue'

const form = defineModel({ type: Object, required: true })
defineProps({ allowInheritance: Boolean, disabled: Boolean })
</script>
