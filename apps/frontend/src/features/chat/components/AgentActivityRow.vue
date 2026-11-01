<template>
  <q-item dense class="activity-row">
    <q-item-section>
      <q-item-label class="work-history__activity-line">
        <slot />
      </q-item-label>
    </q-item-section>
    <slot name="state" />
  </q-item>
</template>
