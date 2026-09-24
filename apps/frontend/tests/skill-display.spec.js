import { describe, expect, it } from 'vitest'

import { skillDisplayDescription, skillDisplayName } from '../src/features/skills/skill-display'

const weather = {
  id: '00000000-0000-4000-8000-000000000101',
  name: 'Weather',
  description: 'Retrieve current weather conditions and forecasts for a location and time range. Use for requests about temperature, rain, snow, wind, or weather-dependent activities. Do not use for translation or general explanations of weather.',
  isDefault: true
}

describe('shipped skill display text', () => {
  it('localizes unchanged defaults without hiding edits', () => {
    expect(skillDisplayName(weather)).toBe('Wetter')
    expect(skillDisplayDescription(weather)).toBe(
      'Ruft aktuelle Wetterbedingungen und Vorhersagen für einen Ort und Zeitraum ab.'
    )
    expect(skillDisplayName({ ...weather, name: 'Outdoor-Wetter' })).toBe('Outdoor-Wetter')
    expect(skillDisplayDescription({ ...weather, description: 'Eigene Beschreibung' }))
      .toBe('Eigene Beschreibung')
  })
})
