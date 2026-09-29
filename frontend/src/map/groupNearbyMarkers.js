export function groupNearbyMarkers(markers, project, radius = 52) {
  const pending = markers.map((marker) => ({ marker, point: project(marker.position) }))
    .sort((left, right) => left.marker.object.id - right.marker.object.id)
  const groups = []

  while (pending.length) {
    const group = [pending.shift()]
    // Bound every group to its anchor; do not join chains across districts.
    {
      const origin = group[0].point
      for (let candidate = pending.length - 1; candidate >= 0; candidate -= 1) {
        const item = pending[candidate]
        if (item.marker.featureCode === group[0].marker.featureCode
          && Math.hypot(origin.x - item.point.x, origin.y - item.point.y) <= radius) {
          group.push(item)
          pending.splice(candidate, 1)
        }
      }
    }
    groups.push(group.map((item) => item.marker).sort((a, b) => a.object.id - b.object.id))
  }

  return groups
}
