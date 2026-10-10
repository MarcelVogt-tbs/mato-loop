import { expect, it } from 'vitest'
import { agentVersionLabel } from '../src/features/computers/version'
import { agentDisplayName } from '../src/features/computers/display'

it.each([
  ['2026-10-08-aaaaaaaa', '2026-10-08-aaaaaaaa', 'Gleicher Stand wie MatoLoop'],
  ['2026-10-07-bbbbbbbb', '2026-10-08-aaaaaaaa', 'Abweichender Stand · älterer Commit'],
  ['2026-10-09-bbbbbbbb', '2026-10-08-aaaaaaaa', 'Abweichender Stand · neuerer Commit'],
  ['2026-10-08-bbbbbbbb', '2026-10-08-aaaaaaaa', 'Abweichender Stand'],
  ['2026-10-08-aaaaaaaa-dirty', '2026-10-08-aaaaaaaa-dirty', 'Lokal verändert'],
  ['2026-10-08-aaaaaaaa', '2026-10-08-aaaaaaaa-dirty', 'Abweichender Stand · MatoLoop lokal verändert'],
  ['0.1.0', '2026-10-08-aaaaaaaa', 'Stand unbekannt'],
  ['2026-10-08-aaaaaaaa', null, 'Stand unbekannt']
])('compares agent %s with backend %s', (agent, backend, label) => {
  expect(agentVersionLabel(agent, backend)).toBe(label)
})

it.each([
  [{ id: '12345678-id', alias: '', computerName: 'host' }, 'host'],
  [{ id: '12345678-id', alias: '', computerName: null }, 'Computer 12345678'],
  [{ id: '12345678-id', alias: 'Custom', computerName: 'host' }, 'Custom'],
  [{ id: '12345678-id', alias: '', displayName: 'Backend name' }, 'Backend name'],
  [{ connected: true }, 'Computer']
])('resolves the computer display name for %j', (agent, name) => {
  expect(agentDisplayName(agent)).toBe(name)
})
