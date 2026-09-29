import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import L from 'leaflet'
import { booleanPointInPolygon, point } from '@turf/turf'

import {
  GeoJSON,
  Marker,
  MapContainer,
  Popup,
  TileLayer,
  useMap,
} from 'react-leaflet'
import { Link } from 'react-router-dom'

import { createSyntheticObjectLocations } from '../map/syntheticObjectLocations'
import { groupNearbyMarkers } from '../map/groupNearbyMarkers'
import { shouldShowDistrictHoverCards } from '../map/districtHoverCards'
import { positionDistrictHoverCard } from '../map/positionDistrictHoverCard'
import {
  createObjectMarkerMarkup,
  getClusterPresentation,
  hasUnresolvedSignal,
} from '../map/objectMarkerPresentation'
import { formatMoscowDateTime } from '../dateTime'
import { useEscapeDismiss } from '../hooks/useEscapeDismiss'

import 'leaflet/dist/leaflet.css'
import '../map/watermelonMap.css'


const brandColors = {
  base: '#EEF1F3',
  border: 'var(--map-boundary-primary, #4A92C9)',
  accent: 'var(--map-boundary-active, #123E66)',
}

function clusterSize(childCount) {
  if (childCount >= 20) return 52
  if (childCount >= 10) return 46
  if (childCount >= 5) return 40
  return 34
}

function createClusterMarkup({ objectCount, openSignalCount }) {
  return `<span class="map-object-cluster"><strong class="map-cluster-count">${objectCount}</strong>${openSignalCount > 0
    ? `<span aria-hidden="true" class="map-object-cluster__signal-count">${openSignalCount}</span>`
    : ''}</span>`
}

function getCode(feature) {
  return (
    feature.properties?.code ??
    feature.properties?.name ??
    feature.properties?.display_name ??
    'Округ'
  )
}

function requiresAttention(object) {
  return typeof object.requires_attention === 'boolean'
    ? object.requires_attention
    : object.risk_flag === true
}

function stopMapClickPropagation(event) {
  if (event.originalEvent) L.DomEvent.stopPropagation(event.originalEvent)
}

function DistrictMapClickHandler({ data, onSelect }) {
  const map = useMap()

  useEffect(() => {
    const handleMapClick = (event) => {
      const markerSelector = '.map-object-cluster-wrap, .watermelon-object-marker-icon'
      const eventTarget = event.originalEvent?.target
      if (eventTarget instanceof Element && eventTarget.closest(markerSelector)) return

      const { lat, lng } = event.latlng
      const feature = data.features.find((item) => (
        booleanPointInPolygon(point([lng, lat]), item)
      ))
      if (feature) onSelect(getCode(feature))
    }

    map.on('click', handleMapClick)
    return () => map.off('click', handleMapClick)
  }, [data, map, onSelect])

  return null
}

function DistrictPointerTracker({ data, onHover }) {
  const map = useMap()
  useEffect(() => {
    const container = map.getContainer()
    const move = event => {
      if (event.target instanceof Element && event.target.closest(
        '.watermelon-object-marker-icon, .object-risk-cluster, .map-cluster-count, .leaflet-popup',
      )) {
        onHover(null, map.mouseEventToContainerPoint(event))
        return
      }
      const location = map.mouseEventToLatLng(event)
      const feature = data.features.find(feature => booleanPointInPolygon(point([location.lng, location.lat]), feature))
      onHover(feature ? getCode(feature) : null, map.mouseEventToContainerPoint(event))
    }
    // Capture at the container, including moves over markers and shared SVG edges.
    container.addEventListener('mousemove', move, true)
    return () => container.removeEventListener('mousemove', move, true)
  }, [data, map, onHover])
  return null
}

function ConfigureMapPanes() {
  const map = useMap()
  useLayoutEffect(() => {
    for (const [name, zIndex] of [['districts', 400], ['district-highlight', 425], ['object-risks', 450]]) {
      const pane = map.getPane(name) ?? map.createPane(name)
      pane.style.zIndex = String(zIndex)
      if (name === 'district-highlight') pane.style.pointerEvents = 'none'
    }
    // Panes belong to this map, whose destruction cleans them up. Setup is idempotent.
  }, [map])
  return null
}


function ObjectRiskMarkers({ markers, selectedOkrug, snapshotTs, stale, replayMode }) {
  const map = useMap()
  const [, setZoomRevision] = useState(0)
  useEffect(() => {
    const guardMarkerClick = (layer) => {
      if (!(layer instanceof L.Marker) || layer._watermelonClickGuard) return
      const icon = layer.getElement()
      if (!icon?.matches('.map-object-cluster-wrap, .watermelon-object-marker-icon')) return

      layer._watermelonClickGuard = true
      const handleIconClick = (event) => {
        L.DomEvent.stopPropagation(event)
        layer.openPopup()
      }
      const cleanup = () => {
        icon.removeEventListener('click', handleIconClick)
        delete layer._watermelonClickGuard
      }
      icon.addEventListener('click', handleIconClick)
      layer.once('remove', cleanup)
    }
    const handleLayerAdd = ({ layer }) => guardMarkerClick(layer)

    map.on('layeradd', handleLayerAdd)
    map.eachLayer(guardMarkerClick)
    return () => map.off('layeradd', handleLayerAdd)
  }, [map])
  useEffect(() => {
    const refresh = () => setZoomRevision((revision) => revision + 1)
    map.on('zoomend', refresh)
    return () => map.off('zoomend', refresh)
  }, [map])

  const visible = markers
    .filter((marker) => !selectedOkrug || marker.featureCode === selectedOkrug)
  const groups = groupNearbyMarkers(
    visible,
    (position) => map.project(position, map.getZoom()),
  )

  return groups.map((group) => {
    if (group.length > 1) {
      // Anchor inside the district rather than a centroid outside a concave polygon.
      const center = group[0].position
      const presentation = getClusterPresentation(group)
      const size = clusterSize(presentation.objectCount)
      const clusterTitle = `${presentation.objectCount} объектов${presentation.openSignalCount > 0
        ? `, открытых сигналов: ${presentation.openSignalCount}`
        : ''}`
      const icon = L.divIcon({
        className: `map-object-cluster-wrap ${presentation.className}`,
        html: createClusterMarkup(presentation),
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
        popupAnchor: [0, -size / 2],
      })
      return (
        <Fragment key={`cluster-${group.map(({ object }) => object.id).join('-')}`}>
          <Marker
            pane="object-risks"
            position={center}
            icon={icon}
            title={clusterTitle}
            alt={clusterTitle}
            keyboard
            riseOnHover
            bubblingMouseEvents={false}
            eventHandlers={{ click: stopMapClickPropagation }}
          >
            <Popup pane="popupPane" maxHeight={280}>
              <div className="watermelon-map-popup-content">
                <strong>Объектов: {presentation.objectCount}</strong>
                <div>Требуют внимания: {group.filter(({ object }) => requiresAttention(object)).length}</div>
                {presentation.openSignalCount > 0 && <div className="watermelon-map-open-signal">Открытых сигналов: {presentation.openSignalCount}</div>}
                <button type="button" className="watermelon-map-action" onClick={() => {
                  map.closePopup()
                  map.fitBounds(L.latLngBounds(group.map(({ position }) => position)), { padding: [48, 48], maxZoom: Math.min(16, map.getZoom() + 2) })
                }}>Показать объекты</button>
              </div>
            </Popup>
          </Marker>
        </Fragment>
      )
    }
    const { object, position } = group[0]
      const positive = object.operational_status === 'available' && object.risk_flag === true
      const openSignal = hasUnresolvedSignal(object)
      const probability = object.risk_probability
      const hasPrediction = object.operational_status === 'available' && Number.isFinite(probability)
      const icon = L.divIcon({
        className: 'watermelon-object-marker-icon',
        html: createObjectMarkerMarkup(object, positive),
        iconSize: [20, 20],
        iconAnchor: [10, 10],
        popupAnchor: [0, -10],
      })

      return (
        <Marker
          key={object.id}
          pane="object-risks"
          position={position}
          icon={icon}
          title={object.name ?? `Объект ${object.id}`}
          alt={object.name ?? `Объект ${object.id}`}
          keyboard
          riseOnHover
          bubblingMouseEvents={false}
          eventHandlers={{ click: stopMapClickPropagation }}
        >
          <Popup pane="popupPane" maxHeight={280}>
            <div className="watermelon-map-popup-content">
              <strong>{object.name ?? `Объект ${object.id}`}</strong>
              <span className="watermelon-map-popup-id">Объект {object.id}</span>
              {openSignal && <div className="watermelon-map-open-signal">Открытый сигнал{object.human_state_label ? ` · ${object.human_state_label}` : ''}</div>}
              {hasPrediction ? (
                <div className="watermelon-map-popup-risk">Риск тревожного события: <strong>{Math.round(probability * 100)}%</strong></div>
              ) : (
                <div className="watermelon-map-popup-unavailable">Оценка временно недоступна</div>
              )}
              <small>Обновлено: {formatMoscowDateTime(object.updated_at ?? snapshotTs)}{replayMode ? ' · Replay / исторический просмотр' : stale ? ' · исторический' : ''}</small>
              <Link className="watermelon-map-action" to={`/objects/${object.id}`}>Открыть объект <span aria-hidden="true">→</span></Link>
            </div>
          </Popup>
        </Marker>
      )
  })
}

function MapZoomControls() {
  const map = useMap()
  const controlsRef = useRef(null)
  const [zoom, setZoom] = useState(map.getZoom())
  useEffect(() => {
    const controls = controlsRef.current
    if (!controls) return undefined
    L.DomEvent.disableClickPropagation(controls)
    L.DomEvent.disableScrollPropagation(controls)
    L.DomEvent.on(controls, 'keydown', L.DomEvent.stopPropagation)
    return () => L.DomEvent.off(controls, 'keydown', L.DomEvent.stopPropagation)
  }, [])
  useEffect(() => {
    const update = () => setZoom(map.getZoom())
    map.on('zoomend', update)
    update()
    return () => map.off('zoomend', update)
  }, [map])

  return (
    <div ref={controlsRef} className="watermelon-map-zoom-controls" aria-label="Масштаб карты">
      <button type="button" aria-label="Приблизить карту" disabled={zoom >= map.getMaxZoom()} onClick={() => map.zoomIn(0.25)}><span aria-hidden="true">+</span></button>
      <button type="button" aria-label="Отдалить карту" disabled={zoom <= map.getMinZoom()} onClick={() => map.zoomOut(0.25)}><span aria-hidden="true">−</span></button>
    </div>
  )
}


function MapViewport({ data, selectedFeature, onZoomChange, onOverviewZoomChange }) {
  const map = useMap()

  useEffect(() => {
    const updateZoom = () => onZoomChange(map.getZoom())
    map.on('zoomend', updateZoom)
    updateZoom()
    return () => map.off('zoomend', updateZoom)
  }, [map, onZoomChange])

  useEffect(() => {
    if (!data) return undefined

    const moscowBounds = L.geoJSON(data).getBounds()
    const maxBounds = moscowBounds.pad(0.12)

    const configureViewport = () => {
      const minZoom = map.getBoundsZoom(
        moscowBounds,
        false,
        L.point(68, 68)
      )
      onOverviewZoomChange(minZoom)

      map.setMaxBounds(maxBounds)
      map.setMinZoom(minZoom)

      if (!selectedFeature) {
        map.fitBounds(moscowBounds, {
          animate: true,
          duration: 0.55,
          padding: [34, 34],
        })
      }
    }

    configureViewport()
    map.on('resize', configureViewport)

    return () => {
      map.off('resize', configureViewport)
    }
  }, [data, map, onOverviewZoomChange, selectedFeature])

  useEffect(() => {
    if (!selectedFeature) return

    map.fitBounds(L.geoJSON(selectedFeature).getBounds(), {
      animate: true,
      duration: 0.55,
      maxZoom: 12.75,
      padding: [48, 48],
    })
  }, [map, selectedFeature])

  return null
}


function SyncMapSize() {
  const map = useMap()

  useEffect(() => {
    const container = map.getContainer()
    let frameId = null

    const updateSize = () => {
      if (frameId !== null) cancelAnimationFrame(frameId)

      frameId = requestAnimationFrame(() => {
        map.invalidateSize({ animate: false, pan: false })
      })
    }

    const observer = new ResizeObserver(updateSize)

    observer.observe(container)
    window.addEventListener('resize', updateSize)
    updateSize()

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', updateSize)

      if (frameId !== null) cancelAnimationFrame(frameId)
    }
  }, [map])

  return null
}

function RegisterMapPopupEscape() {
  const map = useMap()
  const [hasPopup, setHasPopup] = useState(false)
  useEscapeDismiss(hasPopup, () => map.closePopup())

  useEffect(() => {
    const open = () => setHasPopup(true)
    const close = () => setHasPopup(false)
    map.on('popupopen', open)
    map.on('popupclose', close)
    return () => {
      map.off('popupopen', open)
      map.off('popupclose', close)
    }
  }, [map])

  return null
}


function MoscowOkrugsMap({
  onSelectionChange,
  selectedArea,
  objects = [],
  snapshotTs = null,
  stale = true,
  replayMode = false,
  objectsReady = true,
  objectsError = false,
  onRetryObjects,
  hideToolbar = false,
  objectSearch: controlledObjectSearch,
  onObjectSearchChange,
}) {
  const shellRef = useRef(null)
  const [data, setData] = useState(null)
  const [hoveredCode, setHoveredCode] = useState(null)
  const [mapError, setMapError] = useState(null)
  const [mapRetry, setMapRetry] = useState(0)
  const [localSelection, setSelectedOkrug] = useState(null)
  const selectedOkrug = selectedArea === undefined ? localSelection : selectedArea
  const [mapZoom, setMapZoom] = useState(null)
  const [overviewZoom, setOverviewZoom] = useState(null)
  const [hoveredDistrict, setHoveredDistrict] = useState(null)
  const [localObjectSearch, setLocalObjectSearch] = useState('')
  const objectSearch = controlledObjectSearch ?? localObjectSearch
  const setObjectSearch = onObjectSearchChange ?? setLocalObjectSearch

  useEffect(() => {
    let active = true
    fetch('/geo/moscow-okrugs.geojson')
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        return response.json()
      })
      .then((value) => {
        if (!Array.isArray(value?.features) || value.features.length === 0) {
          throw new Error('Файл не содержит границ округов')
        }
        if (!active) return
        setData(value)
        setMapError(null)
      })
      .catch((error) => {
        if (active) setMapError(error.message || 'Неизвестная ошибка загрузки карты')
      })
    return () => { active = false }
  }, [mapRetry])

  const selectedFeature = useMemo(
    () => data?.features.find((feature) => (
      getCode(feature) === selectedOkrug
    )) ?? null,
    [data, selectedOkrug]
  )

  const selectOkrug = (code) => {
    if (code === selectedOkrug) return
    setHoveredDistrict(null)
    setSelectedOkrug(code)
    onSelectionChange?.(code)
  }

  useEscapeDismiss(Boolean(selectedOkrug), () => selectOkrug(null))

  const markers = useMemo(
    () => data ? createSyntheticObjectLocations(objects, data.features) : [],
    [data, objects]
  )
  const searchedMarkers = useMemo(() => {
    const query = objectSearch.trim().toLocaleLowerCase('ru-RU')
    if (!query) return markers
    return markers.filter(({ object }) => (
      `${object.name ?? ''} ${object.id}`.toLocaleLowerCase('ru-RU').includes(query)
    ))
  }, [markers, objectSearch])
  const visibleMarkers = searchedMarkers.filter(({ featureCode }) => (
    !selectedOkrug || featureCode === selectedOkrug
  ))
  const districtStats = useMemo(() => {
    const result = new Map()
    for (const feature of data?.features ?? []) {
      result.set(getCode(feature), { total: 0, available: 0, positive: 0, unavailable: 0, attention: 0 })
    }
    for (const { object, featureCode } of markers) {
      const stats = result.get(featureCode)
      if (!stats) continue
      stats.total += 1
      if (requiresAttention(object)) stats.attention += 1
      if (object.operational_status === 'available') {
        stats.available += 1
        if (object.risk_flag === true) stats.positive += 1
      } else {
        stats.unavailable += 1
      }
    }
    return result
  }, [data, markers])
  const showDistrictHoverCards = shouldShowDistrictHoverCards({
    selectedDistrict: selectedOkrug,
    zoom: mapZoom,
    overviewZoom,
  })
  const hoverCardsEnabledRef = useRef(showDistrictHoverCards)
  useEffect(() => {
    hoverCardsEnabledRef.current = showDistrictHoverCards
  }, [showDistrictHoverCards])
  const styleFeature = () => {
    return {
      color: brandColors.border,
      weight: 1.4,
      opacity: selectedOkrug ? 0 : 0.82,
      fillColor: brandColors.base,
      fillOpacity: 0,
    }
  }

  const onEachFeature = (feature, layer) => {
    const code = getCode(feature)
    const isSelected = selectedOkrug === code

    if (!selectedOkrug || isSelected) {
      layer.bindTooltip(code, {
        permanent: true,
        direction: 'center',
        className: 'okrug-label',
      })
    }

  }

  return (
    <div className="moscow-okrugs-shell" ref={shellRef} onMouseLeave={() => {
      setHoveredCode(null)
      setHoveredDistrict(null)
    }}>
      <MapContainer
        center={[55.75, 37.62]}
        zoom={10}
        zoomSnap={0.25}
        zoomDelta={0.25}
        wheelPxPerZoomLevel={180}
        zoomControl={false}
        attributionControl={false}
        scrollWheelZoom={true}
        maxBoundsViscosity={1}
        className="moscow-okrugs-map"
      >
        <SyncMapSize />
        <ConfigureMapPanes />
        <RegisterMapPopupEscape />
        <MapZoomControls />
        {data && <DistrictMapClickHandler data={data} onSelect={selectOkrug} />}

        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />

        {data && (
          <>
            <DistrictPointerTracker data={data} onHover={(code, location) => {
              setHoveredCode(code)
              if (hoverCardsEnabledRef.current && code) {
                setHoveredDistrict({ code, ...positionDistrictHoverCard(location, { width: shellRef.current?.clientWidth }) })
              } else setHoveredDistrict(null)
            }} />
            <MapViewport
              data={data}
              selectedFeature={selectedFeature}
              onZoomChange={setMapZoom}
              onOverviewZoomChange={setOverviewZoom}
            />

            <GeoJSON
              pane="districts"
              key={selectedOkrug ?? 'default'}
              data={data}
              interactive={false}
              style={styleFeature}
              onEachFeature={onEachFeature}
            />
              {data.features.filter(feature => selectedOkrug
                ? getCode(feature) === selectedOkrug
                : getCode(feature) === hoveredCode).map(feature => (
                <GeoJSON pane="district-highlight" key={getCode(feature)} data={feature} interactive={false}
                  style={{
                    color: brandColors.accent,
                    weight: selectedOkrug ? 4 : 2.6,
                    opacity: selectedOkrug ? 1 : 0.95,
                    fill: false,
                  }} />
              ))}
            <ObjectRiskMarkers
              markers={searchedMarkers}
              selectedOkrug={selectedOkrug}
              snapshotTs={snapshotTs}
              stale={stale}
              replayMode={replayMode}
            />
          </>
        )}
      </MapContainer>

      {!hideToolbar && <div className="watermelon-map-toolbar">
        <label className="watermelon-map-location">
          <span className="sr-only">Фильтр по округу</span>
          <select
            value={selectedOkrug ?? ''}
            onChange={(event) => selectOkrug(event.target.value || null)}
            disabled={!data}
          >
            <option value="">Все округа</option>
            {(data?.features ?? []).map((feature) => {
              const code = getCode(feature)
              return <option key={code} value={code}>{code}</option>
            })}
          </select>
        </label>
        <label className="watermelon-map-search">
          <span aria-hidden="true">⌕</span>
          <span className="sr-only">Поиск объекта по названию или номеру</span>
          <input
            type="search"
            value={objectSearch}
            onChange={(event) => setObjectSearch(event.target.value)}
            placeholder="Найти объект"
            disabled={!objectsReady || objectsError}
          />
        </label>
      </div>}

      <div className="moscow-map-title">
        <span>Москва</span>
        <h2>Округа</h2>
        {replayMode && <b className="watermelon-map-replay">Replay · исторический просмотр</b>}
        <small>Расположение объектов схематическое</small>
      </div>

      {selectedOkrug && (
        <div className="moscow-map-drilldown">
          <button
            type="button"
            className="moscow-map-back"
            onClick={() => selectOkrug(null)}
          >
            <span aria-hidden="true">←</span>
            Все округа
          </button>

          <div className="moscow-map-selection" aria-live="polite">
            <strong>{selectedOkrug}</strong>
            <span>Объекты округа</span>
          </div>
        </div>
      )}

      {showDistrictHoverCards && hoveredDistrict && (() => {
        const stats = districtStats.get(hoveredDistrict.code)
          ?? { total: 0, available: 0, positive: 0, unavailable: 0, attention: 0 }
        return (
          <div
            className={`okrug-hover-card is-${hoveredDistrict.horizontal} is-${hoveredDistrict.vertical}`}
            style={{ left: hoveredDistrict.x, top: hoveredDistrict.y }}
            aria-live="polite"
          >
            <strong>{hoveredDistrict.code}</strong>
            {stats.attention > 0 ? <span>Требуют внимания: {stats.attention}</span> : <span>Нет объектов, требующих внимания</span>}
            {stats.unavailable > 0 && <span>Без актуальной оценки: {stats.unavailable}</span>}
            <span className="district-hover-secondary">Объектов: {stats.total}</span>
          </div>
        )
      })()}

      {!data && !mapError && (
        <div className="moscow-map-loading">
          Загрузка карты...
        </div>
      )}
      {!data && mapError && (
        <div className="moscow-map-error" role="alert">
          <strong>Не удалось загрузить границы округов</strong>
          <span>{mapError}</span>
          <button type="button" onClick={() => setMapRetry((attempt) => attempt + 1)}>Повторить</button>
        </div>
      )}
      {!objectsError && !objectsReady && (
        <div className="watermelon-map-data-state is-loading" role="status">
          Загрузка operational-снимка объектов…
        </div>
      )}
      {objectsError && (
        <div className="watermelon-map-data-state is-error" role="alert">
          <strong>Состояние объектов неизвестно</strong>
          <span>Не удалось обновить operational-снимок карты.</span>
          {onRetryObjects && <button type="button" onClick={onRetryObjects}>Повторить</button>}
        </div>
      )}
      {data && objectsReady && !objectsError && visibleMarkers.length === 0 && (
        <div className="watermelon-map-data-state is-empty" role="status">
          <strong>{objectSearch.trim() ? 'Ничего не найдено' : 'Объектов нет'}</strong>
          <span>{objectSearch.trim()
            ? 'Измените запрос или очистите поиск.'
            : selectedOkrug ? 'В выбранном округе нет доступных объектов.' : 'В текущем operational-снимке нет доступных объектов.'}</span>
        </div>
      )}
    </div>
  )
}


export default MoscowOkrugsMap
