import { Dialog } from 'quasar'

export function useConfirm() {
  function confirm(
    message = 'Soll die Aktion wirklich ausgeführt werden?',
    title = 'Bestätigung',
    { okLabel = 'Bestätigen', okColor = 'negative', cancelLabel = 'Abbrechen' } = {}
  ) {
    return new Promise((resolve) => {
      Dialog.create({
        title,
        message,
        ok: { label: okLabel, color: okColor, flat: false },
        cancel: { label: cancelLabel, flat: true }
      })
        .onOk(() => resolve(true))
        .onCancel(() => resolve(false))
        .onDismiss(() => resolve(false))
    })
  }

  return { confirm }
}
