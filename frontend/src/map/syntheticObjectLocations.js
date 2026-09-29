import {
  bbox,
  booleanPointInPolygon,
  point,
  pointOnFeature,
} from '@turf/turf'

function featureCode(feature) {
  return feature.properties?.code
    ?? feature.properties?.name
    ?? feature.properties?.display_name
    ?? 'Округ'
}

export function createSyntheticObjectLocations(objects, features) {
  if (!features?.length) return []

  return objects.map((object) => {
    const objectId = Number(object.id)
    const seed = Math.imul(objectId | 0, 2654435761) >>> 0
    const feature = features[seed % features.length]
    const [minLongitude, minLatitude, maxLongitude, maxLatitude] = bbox(feature)
    let state = seed || 1
    const nextRandom = () => {
      state = (Math.imul(1664525, state) + 1013904223) >>> 0
      return state / 4294967296
    }

    for (let attempt = 0; attempt < 500; attempt += 1) {
      const longitude = minLongitude + nextRandom() * (maxLongitude - minLongitude)
      const latitude = minLatitude + nextRandom() * (maxLatitude - minLatitude)
      if (booleanPointInPolygon(point([longitude, latitude]), feature)) {
        return {
          object,
          featureCode: featureCode(feature),
          position: [latitude, longitude],
        }
      }
    }

    const fallback = pointOnFeature(feature).geometry.coordinates
    return {
      object,
      featureCode: featureCode(feature),
      position: [fallback[1], fallback[0]],
    }
  })
}
