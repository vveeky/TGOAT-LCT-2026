import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { groupNearbyMarkers } from '../src/map/groupNearbyMarkers.js'
import {
  createObjectMarkerMarkup,
  getClusterPresentation,
  hasUnresolvedSignal,
} from '../src/map/objectMarkerPresentation.js'
import { shouldShowDistrictHoverCards } from '../src/map/districtHoverCards.js'
import { positionDistrictHoverCard } from '../src/map/positionDistrictHoverCard.js'
import { parseReplayStatus, replayRefreshInterval } from '../src/replay/replayStatus.js'


test('replay status validates mode/state and uses accelerated polling only for replay', () => {
  assert.deepEqual(parseReplayStatus({ mode: 'normal', state: 'disabled' }), {
    mode: 'normal', state: 'disabled',
  })
  const status = parseReplayStatus({
    mode: 'replay', state: 'running', speed: 1800, progress: 1.5,
    covered_object_count: 77, virtual_time: '2026-05-05T08:59:59Z',
  })
  assert.equal(status.progress, 1)
  assert.equal(replayRefreshInterval(status), 1200)
  assert.equal(replayRefreshInterval({ mode: 'normal' }), 30_000)
  assert.throws(() => parseReplayStatus({ mode: 'replay', state: 'unknown' }), TypeError)
})


test('map clusters nearby object markers without rewriting their operational scores', () => {
  const objects = [
    { id: 10, risk_probability: 0.81, operational_status: 'available' },
    { id: 11, risk_probability: null, operational_status: 'degraded' },
    { id: 12, risk_probability: 0.16, operational_status: 'available' },
  ]
  const markers = objects.map((object, index) => ({ object, position: [index * 10, 0] }))
  const groups = groupNearbyMarkers(markers, ([x, y]) => ({ x, y }), 52)
  assert.deepEqual(groups.map((group) => group.map(({ object }) => object.id)), [[10, 11, 12]])
  assert.deepEqual(groups[0].map(({ object }) => object.risk_probability), [0.81, null, 0.16])
})


test('map has no interactive synthetic hex layer and retains the object detail link', () => {
  const source = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /SVGOverlay|HexGrid|createHexGrid/)
  assert.match(source, /<Link className="watermelon-map-action" to=\{`\/objects\/\$\{object\.id\}`}\s*>Открыть объект/)
  assert.match(source, /useEscapeDismiss\(Boolean\(selectedOkrug\),\s*\(\) => selectOkrug\(null\)\)/)
  assert.match(source, /className="moscow-map-back"/)
  assert.match(source, /className=\{`okrug-hover-card is-\$\{hoveredDistrict\.horizontal\} is-\$\{hoveredDistrict\.vertical\}`\}/)
  assert.match(source, /if \(hoverCardsEnabledRef\.current && code\)/)
  assert.match(source, /container\.addEventListener\('mousemove', move, true\)/)
  assert.match(source, /watermelon-object-marker-icon, \.object-risk-cluster, \.map-cluster-count, \.leaflet-popup/)
})


test('district hover cards are restricted to the overview and disappear when drilled in', () => {
  assert.equal(shouldShowDistrictHoverCards({
    selectedDistrict: null,
    zoom: 8,
    overviewZoom: 8,
  }), true)
  assert.equal(shouldShowDistrictHoverCards({
    selectedDistrict: null,
    zoom: 8.25,
    overviewZoom: 8,
  }), false)
  assert.equal(shouldShowDistrictHoverCards({
    selectedDistrict: 'СЗАО',
    zoom: 8,
    overviewZoom: 8,
  }), false)
  assert.equal(shouldShowDistrictHoverCards({
    selectedDistrict: null,
    zoom: null,
    overviewZoom: 8,
  }), false)
})


test('changing the hover-card zoom gate does not remount district GeoJSON labels', () => {
  const source = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  assert.match(source, /hoverCardsEnabledRef\.current = showDistrictHoverCards/)
  assert.match(source, /key=\{selectedOkrug \?\? 'default'\}/)
  assert.doesNotMatch(source, /key=\{`\$\{selectedOkrug \?\? 'default'\}-\$\{showDistrictHoverCards\}`\}/)
})


test('Ice Blue district outlines stay beneath risk markers in the single theme', () => {
  const source = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  assert.match(source, /border:\s*'var\(--map-boundary-primary, #4A92C9\)'/)
  assert.match(source, /accent:\s*'var\(--map-boundary-active, #123E66\)'/)
  assert.doesNotMatch(source, /bringToFront/)
  assert.match(source, /pane="object-risks"/)
  assert.match(source, /pane="district-highlight"/)
  assert.match(source, /map\.getPane\(name\) \?\? map\.createPane\(name\)/)
  assert.match(source, /interactive=\{false\}/)
  assert.match(source, /weight: 1\.4,[\s\S]*?opacity: selectedOkrug \? 0 : 0\.82/)
  assert.match(source, /color: brandColors\.accent/)
  assert.match(source, /selectedOkrug \? 1 : 0\.95/)
  assert.match(source, /selectedOkrug\s*\?\s*getCode\(feature\) === selectedOkrug\s*:\s*getCode\(feature\) === hoveredCode/)
  assert.doesNotMatch(source, /event\.target\.setStyle/)
  assert.match(source, /<Popup pane="popupPane"/)
})

test('Ice Blue palette is limited to the light dashboard basemap and OSM tile pane', () => {
  const css = readFileSync(new URL('../src/map/watermelonMap.css', import.meta.url), 'utf8')
  assert.match(css, /\.dashboard-page \.moscow-okrugs-shell \{[\s\S]*--map-ground: #C5D9E9/)
  assert.match(css, /--map-urban: #D6E4EF/)
  assert.match(css, /--map-water: #86B7D9/)
  assert.match(css, /--map-boundary-primary: #4A92C9/)
  assert.match(css, /--map-boundary-active: #123E66/)
  assert.match(css, /--map-label-muted: #617F9B/)
  assert.match(css, /\.dashboard-page \.moscow-okrugs-map \.leaflet-tile-pane \{[\s\S]*filter: var\(--map-tile-filter\)/)
  assert.doesNotMatch(css, /\.moscow-okrugs-map\s*\{[^}]*filter:/)
})

test('clicking an object marker or cluster does not select the district underneath it', () => {
  const source = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  assert.match(source, /function DistrictMapClickHandler\(\{ data, onSelect \}\)/)
  assert.match(source, /map\.on\('click', handleMapClick\)/)
  assert.match(source, /const markerSelector = '\.map-object-cluster-wrap, \.watermelon-object-marker-icon'/)
  assert.match(source, /eventTarget\.closest\(markerSelector\)/)
  assert.match(source, /const guardMarkerClick = \(layer\)/)
  assert.match(source, /layer\.openPopup\(\)/)
  assert.match(source, /icon\.addEventListener\('click', handleIconClick\)/)
  assert.match(source, /<GeoJSON[\s\S]*?interactive=\{false\}[\s\S]*?onEachFeature=\{onEachFeature\}/)
  assert.match(source, /eventHandlers=\{\{ click: stopMapClickPropagation \}\}/)
})

test('clusters never cross district borders or join unbounded chains and conserve all objects', () => {
  const markers = [
    { object: { id: 1, risk_flag: true }, featureCode: 'A', position: [0, 0] },
    { object: { id: 2 }, featureCode: 'A', position: [40, 0] },
    { object: { id: 3 }, featureCode: 'A', position: [80, 0] },
    { object: { id: 4 }, featureCode: 'B', position: [0, 0] },
  ]
  const project = ([x, y]) => ({ x, y })
  const ids = (groups) => groups.map(group => group.map(marker => marker.object.id))
  assert.deepEqual(ids(groupNearbyMarkers(markers, project)), [[1, 2], [3], [4]])
  assert.deepEqual(ids(groupNearbyMarkers([...markers].reverse(), project)), [[1, 2], [3], [4]])
  assert.deepEqual(ids(groupNearbyMarkers(markers, ([x, y]) => ({ x: x + 100, y: y - 100 }))), [[1, 2], [3], [4]])
  const expanded = groupNearbyMarkers(markers, ([x, y]) => ({ x: x * 10, y: y * 10 }))
  assert.equal(expanded.flat().length, 4)
  assert.equal(expanded.flat().filter(marker => marker.object.risk_flag === true).length, 1)
})

test('district hover card flips at horizontal and vertical map edges', () => {
  assert.deepEqual(
    positionDistrictHoverCard({ x: 12, y: 300 }, { width: 470, height: 800 }),
    { x: 12, y: 300, horizontal: 'right', vertical: 'above' },
  )
  assert.deepEqual(
    positionDistrictHoverCard({ x: 450, y: 12 }, { width: 470, height: 800 }),
    { x: 450, y: 12, horizontal: 'left', vertical: 'below' },
  )
})

test('map keeps current risk separate from unresolved human signal and uses smaller wheel increments', () => {
  const source = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  assert.match(source, /wheelPxPerZoomLevel=\{180\}/)
  assert.match(source, /zoomDelta=\{0\.25\}/)
  assert.match(source, /createObjectMarkerMarkup\(object, positive\)/)
  assert.match(source, /Открытый сигнал/)
  assert.match(source, /getClusterPresentation\(group\)/)
  assert.doesNotMatch(source, /Размещение объектов синтетическое/)
})

test('object popup stays bound to the accessible Watermelon marker without shifting its Leaflet anchor', () => {
  const source = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('../src/map/watermelonMap.css', import.meta.url), 'utf8')
  assert.match(source, /<Marker\s+[\s\S]*?icon=\{icon\}[\s\S]*?keyboard[\s\S]*?bubblingMouseEvents=\{false\}[\s\S]*?<Popup pane="popupPane"/)
  assert.match(source, /<Marker\s+key=\{object\.id\}/)
  assert.doesNotMatch(styles, /watermelon-object-marker-icon\s*\{[^}]*margin:\s*0\s*!important/s)
})

test('unresolved signal adds a compact independent corner badge to the object marker', () => {
  const markup = createObjectMarkerMarkup({
    id: 42,
    operational_status: 'available',
    risk_level: 'high',
    requires_attention: true,
  })
  assert.match(markup, /watermelon-object-marker is-high/)
  assert.match(markup, /watermelon-object-marker__signal/)
  assert.match(markup, /watermelon-object-marker__dot/)
  assert.equal(hasUnresolvedSignal({ requires_attention: true }), true)
})

test('objects without unresolved signal do not render the signal badge', () => {
  const markup = createObjectMarkerMarkup({
    id: 43,
    operational_status: 'available',
    risk_level: 'critical',
    requires_attention: false,
    human_state: 'resolved',
  })
  assert.match(markup, /watermelon-object-marker is-critical/)
  assert.doesNotMatch(markup, /watermelon-object-marker__signal/)
  assert.equal(hasUnresolvedSignal({ requires_attention: false, human_state: 'resolved' }), false)
})

test('large unresolved-signal halos are absent and the replacement badge stays compact', () => {
  const component = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  const presentation = readFileSync(new URL('../src/map/objectMarkerPresentation.js', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('../src/map/watermelonMap.css', import.meta.url), 'utf8')
  const stageStyles = readFileSync(new URL('../src/stage13-visual.css', import.meta.url), 'utf8')
  assert.doesNotMatch(`${component}\n${presentation}\n${styles}\n${stageStyles}`, /object-risk-open-signal-ring/)
  assert.match(styles, /\.watermelon-object-marker__signal\s*\{[^}]*width:\s*7px;[^}]*height:\s*7px;/s)
  assert.doesNotMatch(styles, /watermelon-object-marker__signal[^}]*animation:/s)
})

test('cluster presentation stays neutral across child risk values and exposes open signals separately', () => {
  const cluster = getClusterPresentation([
    { object: { risk_level: 'critical', requires_attention: false } },
    { object: { risk_level: 'low', human_state: 'monitoring' } },
  ])
  assert.deepEqual(cluster, {
    className: 'object-risk-cluster',
    objectCount: 2,
    openSignalCount: 1,
  })
  const otherRiskMix = getClusterPresentation([
    { object: { risk_level: 'no-data', requires_attention: false } },
    { object: { risk_level: 'medium', human_state: 'monitoring' } },
  ])
  assert.equal(otherRiskMix.className, cluster.className)
  assert.equal(Object.hasOwn(cluster, 'riskLevel'), false)
})

test('map receives scoped objects from its parent and never fetches its own object list', () => {
  const source = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  assert.match(source, /objects = \[\]/)
  assert.match(source, /createSyntheticObjectLocations\(objects, data\.features\)/)
  assert.doesNotMatch(source, /getObjects|fetch\([^)]*(?:\/objects|objects\?)/)
  assert.doesNotMatch(source, /mock\/map-hierarchy|loadMapHierarchyMock/)
})

test('Replay polling passes updated operational object snapshots into the map', () => {
  const dashboard = readFileSync(new URL('../src/pages/DashboardPage.jsx', import.meta.url), 'utf8')
  assert.match(dashboard, /replayStatus\?\.mode === 'replay'/)
  assert.match(dashboard, /objects=\{dashboard\?\.objects \?\? \[\]\}/)
  assert.match(dashboard, /snapshotTs=\{dashboard\?\.snapshot_ts \?\? null\}/)
  assert.match(dashboard, /stale=\{dashboard\?\.stale \?\? true\}/)
  assert.match(dashboard, /objectsReady=\{Boolean\(dashboard\)\}/)
})

test('dashboard keeps replay and operational snapshot props while moving ReplayControls into the map menu', () => {
  const dashboard = readFileSync(new URL('../src/pages/DashboardPage.jsx', import.meta.url), 'utf8')
  assert.match(dashboard, /import MapControlsMenu from '\.\.\/components\/MapControlsMenu'/)
  assert.match(dashboard, /snapshotTs=\{dashboard\?\.snapshot_ts \?\? null\}/)
  assert.match(dashboard, /stale=\{dashboard\?\.stale \?\? true\}/)
  assert.match(dashboard, /replayMode=\{replayStatus\?\.mode === 'replay'\}/)
  assert.match(dashboard, /objectsError=\{Boolean\(error\)\}/)
  assert.match(dashboard, /onRetryObjects=\{\(\) => setRetryKey/)
  assert.match(dashboard, /<MapControlsMenu label="Управление симуляцией">[\s\S]*?<ReplayControls \/>[\s\S]*?<\/MapControlsMenu>/)
  assert.equal((dashboard.match(/<ReplayControls \/>/g) ?? []).length, 1)
})

test('dispatcher map menu preserves replay, freshness, area selection, and show-all controls', () => {
  const dispatcher = readFileSync(new URL('../src/pages/DispatcherMonitoringPage.jsx', import.meta.url), 'utf8')
  assert.match(dispatcher, /replayMode=\{status\?\.mode === 'replay'\}/)
  assert.match(dispatcher, /snapshotTs=\{data\?\.snapshot_ts \?\? \(status\?\.mode === 'replay' \? status\.virtual_time : null\)\}/)
  assert.match(dispatcher, /stale=\{data\?\.stale \?\? false\}/)
  assert.match(dispatcher, /objectsReady=\{Boolean\(data\)\}/)
  assert.match(dispatcher, /objectsError=\{Boolean\(error\)\}/)
  assert.match(dispatcher, /onRetryObjects=\{\(\) => setRetry/)
  assert.match(dispatcher, /selectedArea=\{area\}[\s\S]*?onSelectionChange=\{setArea\}/)
  assert.match(dispatcher, /createPortal\([\s\S]*?sidebar-map-district-list[\s\S]*?Все округа/)
  assert.match(dispatcher, /features\.map\(feature =>[\s\S]*?sidebar-map-district-option[\s\S]*?onClick=\{\(\) => setArea\(code\)\}/)
  assert.doesNotMatch(dispatcher, /Найти объект|Градация риска/)
  assert.match(dispatcher, /<MapControlsMenu label="Управление мониторингом">[\s\S]*?<ReplayControls \/>[\s\S]*?checked=\{showAll\}[\s\S]*?<\/MapControlsMenu>/)
  assert.doesNotMatch(dispatcher, /dispatcher-monitoring-controls/)
})

test('map controls menu dismisses accessibly and keeps its styles scoped to the Watermelon map stylesheet', () => {
  const menu = readFileSync(new URL('../src/components/MapControlsMenu.jsx', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('../src/map/watermelonMap.css', import.meta.url), 'utf8')
  const globalStyles = readFileSync(new URL('../src/stage13-visual.css', import.meta.url), 'utf8')
  assert.match(menu, /useEscapeDismiss\(open, close\)/)
  assert.match(menu, /addEventListener\('pointerdown', dismissOnOutsideClick, true\)/)
  assert.match(menu, /aria-expanded=\{open\}/)
  assert.match(styles, /\.dashboard-page \.map-controls-menu\s*\{/)
  assert.match(styles, /\.dashboard-page \.map-controls-menu-popover\s*\{/)
  assert.match(styles, /\.dashboard-page \.map-controls-menu \.replay-controls\s*\{/)
  assert.match(styles, /\.dashboard-page \.map-controls-menu-trigger\s*\{[^}]*width:\s*42px;[^}]*height:\s*42px;/s)
  assert.match(styles, /\.dashboard-page \.map-controls-menu-popover\s*\{[^}]*bottom:\s*calc\(100% \+ 8px\);/s)
  assert.match(styles, /align-self:\s*end;[^}]*justify-self:\s*end;[^}]*margin:\s*12px;/s)
  assert.doesNotMatch(styles, /\.dashboard-map-region:has\(> \.map-controls-menu\) \.watermelon-map-toolbar/)
  assert.match(styles, /\.sidebar-map-district-list\s*\{[^}]*overflow-y:\s*auto;/s)
  assert.match(styles, /\.sidebar-map-district-option\.is-selected\s*\{/)
  assert.doesNotMatch(globalStyles, /\.map-controls-menu/)
})

test('runtime map clustering stays on district-bounded Stage 13 grouping', () => {
  const source = readFileSync(new URL('../src/components/MoscowOkrugsMap.jsx', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('../src/map/watermelonMap.css', import.meta.url), 'utf8')
  assert.match(source, /groupNearbyMarkers\(/)
  assert.doesNotMatch(source, /MarkerClusterGroup|react-leaflet-cluster/)
  assert.match(source, /className: `map-object-cluster-wrap \$\{presentation\.className\}`/)
  assert.match(source, /class="map-object-cluster"><strong class="map-cluster-count">\$\{objectCount\}/)
  assert.doesNotMatch(source, /<Tooltip pane="tooltipPane" permanent/)
  assert.match(styles, /\.map-object-cluster\s*\{[^}]*background:\s*rgba\(248, 250, 252, 0\.86\)/s)
  assert.match(styles, /\.map-object-cluster__signal-count\s*\{/)
})

test('risk distribution segments scale within the chart and zero-value categories are omitted', () => {
  const source = readFileSync(new URL('../src/components/ui/risk-distribution-widget.jsx', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('../src/stage13-visual.css', import.meta.url), 'utf8')
  assert.match(source, /risk\.value === null \|\| risk\.value <= 0 \|\| totalObjects <= 0/)
  assert.match(source, /share <= 3 \? ' is-small-share' : ''/)
  assert.match(source, /'--department-share': risk\.value/)
  assert.doesNotMatch(source, /'--department-width'/)
  assert.match(styles, /dashboard-load-segment\s*\{[^}]*flex:\s*var\(--department-share\) 1 0px;[^}]*width:\s*0;/s)
  assert.match(styles, /\.dashboard-widget-slot\.is-status > \.dashboard-status-card \.dashboard-load-segment\s*\{[^}]*flex:\s*var\(--department-share\) 1 0px;/s)
  assert.match(styles, /\.dashboard-load-segment\.is-small-share\s*\{\s*min-width:\s*3%;\s*\}/)
  assert.match(styles, /@container dashboard-widget-cell \(max-height: 180px\)/)
})

test('risk widget API errors use a visible retry control', () => {
  const widgets = readFileSync(new URL('../src/dashboard/DispatcherDashboardWidgets.jsx', import.meta.url), 'utf8')
  const styles = readFileSync(new URL('../src/stage13-visual.css', import.meta.url), 'utf8')
  assert.match(widgets, /dashboard-risk-error-state/)
  assert.match(widgets, /dashboard-risk-retry/)
  assert.match(styles, /\.dashboard-risk-error-state\s*\{/)
  assert.match(styles, /\.dashboard-risk-retry:focus-visible\s*\{/)
})
