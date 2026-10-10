const shippedSkills = {
  '00000000-0000-4000-8000-000000000101': {
    name: ['weather', 'Wetter'],
    description: [
      'Retrieve current weather conditions and forecasts for a location and time range. Use for requests about temperature, rain, snow, wind, or weather-dependent activities. Do not use for translation or general explanations of weather.',
      'Ruft aktuelle Wetterbedingungen und Vorhersagen für einen Ort und Zeitraum ab.'
    ]
  }
}

function shippedText(skill, field) {
  const text = skill.isDefault && shippedSkills[skill.id]?.[field]
  return text && skill[field] === text[0] ? text[1] : skill[field]
}

export const skillDisplayName = (skill) => shippedText(skill, 'name')
export const skillDisplayDescription = (skill) => shippedText(skill, 'description')
