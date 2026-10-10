export function agentVersionLabel(version, backendVersion) {
  const pattern = /^(\d{4}-\d{2}-\d{2})-[0-9a-f]{8}(-dirty)?$/
  const agent = pattern.exec(version || '')
  const backend = pattern.exec(backendVersion || '')
  if (agent?.[2]) return 'Lokal verändert'
  if (!agent || !backend) return 'Stand unbekannt'
  if (backend[2]) return 'Abweichender Stand · MatoLoop lokal verändert'
  if (version === backendVersion) return 'Gleicher Stand wie MatoLoop'
  if (agent[1] < backend[1]) return 'Abweichender Stand · älterer Commit'
  if (agent[1] > backend[1]) return 'Abweichender Stand · neuerer Commit'
  return 'Abweichender Stand'
}
