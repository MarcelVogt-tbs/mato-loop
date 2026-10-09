<template>
  <q-page class="column flex-center q-pa-lg text-center">
    <div class="text-h2 text-weight-bold">404</div>
    <div class="text-h6 q-mt-sm">Diese Seite wurde nicht gefunden.</div>
    <q-btn to="/" color="primary" unelevated no-caps class="q-mt-lg" :label="`Zurück zu ${appearance.appName}`" />
  </q-page>
</template>

<script setup>
import { useAppearanceStore } from 'src/features/settings/appearance-store'

const appearance = useAppearanceStore()
defineOptions({ name: 'NotFoundPage' })
</script>
