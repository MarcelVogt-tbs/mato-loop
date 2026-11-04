const refreshes = new WeakMap()

export function queueRefresh(store, config, read, revisionKey = 'revision') {
  if (!refreshes.has(store)) refreshes.set(store, new Map())
  const requests = refreshes.get(store)
  const previous = requests.get(revisionKey)
  if (previous?.queued && previous.revision === store[revisionKey]
    && Object.keys(config || {}).length === Object.keys(previous.config || {}).length
    && Object.keys(config || {}).every((key) => config[key] === previous.config[key])) return previous.promise

  const revision = ++store[revisionKey]
  const next = { config, revision, queued: Boolean(previous), promise: null }
  const run = () => {
    next.queued = false
    if (revision !== store[revisionKey]) return
    return read(revision)
  }
  const request = previous ? previous.promise.catch(() => {}).then(run) : run()
  next.promise = Promise.resolve(request).finally(() => {
    if (requests.get(revisionKey) === next) requests.delete(revisionKey)
  })
  requests.set(revisionKey, next)
  return next.promise
}
