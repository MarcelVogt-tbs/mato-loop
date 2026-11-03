export function dreamRunMeta(status) {
  return ({
    queued: { label: 'Eingeplant', icon: 'schedule', color: 'grey-7' },
    running: { label: 'Wird verarbeitet', icon: 'sync', color: 'primary' },
    blocked: { label: 'Blockiert', icon: 'block', color: 'warning' },
    completed: { label: 'Abgeschlossen', icon: 'check_circle', color: 'positive' },
    failed: { label: 'Fehlgeschlagen', icon: 'error', color: 'negative' },
    cancelled: { label: 'Abgebrochen', icon: 'cancel', color: 'grey-7' }
  })[status] || { label: status || 'Unbekannt', icon: 'help', color: 'grey-7' }
}

export function summaryStatusLabel(status) {
  return ({ disabled: 'Nicht erstellt', pending: 'Ausstehend', completed: 'Aktualisiert', failed: 'Fehlgeschlagen' })[status] || 'Nicht erstellt'
}
