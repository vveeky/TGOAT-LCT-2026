export function shouldShowDistrictHoverCards({
  selectedDistrict,
  zoom,
  overviewZoom,
}) {
  return !selectedDistrict
    && Number.isFinite(zoom)
    && Number.isFinite(overviewZoom)
    && zoom <= overviewZoom + 0.05
}
