import { useEffect, useMemo, useState } from 'react'

import {
  MapContainer,
  TileLayer,
  Polygon,
  GeoJSON,
  useMap,
} from 'react-leaflet'

import {
  polygonToCells,
  cellToBoundary,
} from 'h3-js'

import {
  polygon as turfPolygon,
  transformScale,
} from '@turf/turf'

import 'leaflet/dist/leaflet.css'


/* =====================================================
   MOCK DISTRICT
===================================================== */

const districtGeoJson = {
  type: 'Feature',

  properties: {
    name: 'Останкинский район',
  },

  geometry: {
    type: 'Polygon',

    coordinates: [[
      [37.605, 55.835],
      [37.655, 55.835],
      [37.675, 55.815],
      [37.668, 55.790],
      [37.645, 55.775],
      [37.610, 55.782],
      [37.590, 55.805],
      [37.605, 55.835],
    ]],
  },
}


/* =====================================================
   SETTINGS
===================================================== */

const H3_RESOLUTION = 9


/* =====================================================
   MOCK RISK
===================================================== */

function seededValue(index) {
  return ((index * 47 + 19) % 100) / 100
}


function getRisk(index) {
  const value = seededValue(index)

  if (value > 0.97) {
    return 'critical'
  }

  if (value > 0.90) {
    return 'high'
  }

  if (value > 0.78) {
    return 'medium'
  }

  return 'normal'
}


/* =====================================================
   RISK STYLES
===================================================== */

const riskStyles = {
  normal: {
    fillColor: '#d8d8d2',
    color: '#b9b9b2',
    fillOpacity: 0.36,
    weight: 0.55,
  },

  medium: {
    fillColor: '#ffc4aa',
    color: '#ff9c77',
    fillOpacity: 0.58,
    weight: 0.7,
  },

  high: {
    fillColor: '#ff7540',
    color: '#ff6630',
    fillOpacity: 0.86,
    weight: 0.8,
  },

  critical: {
    fillColor: '#ff4b0b',
    color: '#e43d00',
    fillOpacity: 0.98,
    weight: 0.9,
  },
}


/* =====================================================
   FIT DISTRICT
===================================================== */

function FitDistrict({ data }) {
  const map = useMap()

  useEffect(() => {
    const coordinates =
      data.geometry.coordinates[0]

    const latLngs =
      coordinates.map(([lng, lat]) => [
        lat,
        lng,
      ])

    map.fitBounds(latLngs, {
      padding: [40, 40],
    })
  }, [data, map])

  return null
}


/* =====================================================
   COMPONENT
===================================================== */

function DistrictMap() {
  const [hoveredHex, setHoveredHex] =
    useState(null)

  const [selectedHex, setSelectedHex] =
    useState(null)


  /* ===================================================
     GENERATE HEXES
  =================================================== */

  const hexes = useMemo(() => {
    const polygonForH3 =
      districtGeoJson.geometry.coordinates[0]
        .map(([lng, lat]) => [
          lat,
          lng,
        ])

    const cells = polygonToCells(
      polygonForH3,
      H3_RESOLUTION
    )

    return cells.map((cell, index) => {
      const boundary =
        cellToBoundary(cell)

      return {
        id: cell,
        index,

        risk: getRisk(index),

        positions: boundary.map(
          ([lat, lng]) => [
            lat,
            lng,
          ]
        ),
      }
    })
  }, [])


  /* ===================================================
     HOVER COPY
  =================================================== */

  const hoverPolygon = useMemo(() => {
    if (!hoveredHex) {
      return null
    }

    const coords =
      hoveredHex.positions.map(
        ([lat, lng]) => [
          lng,
          lat,
        ]
      )

    coords.push(coords[0])

    const polygon =
      turfPolygon([coords])

    return transformScale(
      polygon,
      1.16,
      {
        origin: 'centroid',
      }
    )
  }, [hoveredHex])


  const attentionCount =
    hexes.filter(
      (hex) =>
        hex.risk !== 'normal'
    ).length


  return (
    <div className="district-map-shell">

      {/* =================================================
          MAP
      ================================================= */}

      <MapContainer
        center={[55.805, 37.63]}
        zoom={13}

        zoomControl={true}

        scrollWheelZoom={true}

        className="district-map"
      >

        {/* ===============================================
            BASE MAP
        =============================================== */}

        <TileLayer
          attribution="&copy; OpenStreetMap contributors"

          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />


        {/* ===============================================
            AUTO FIT
        =============================================== */}

        <FitDistrict
          data={districtGeoJson}
        />


        {/* ===============================================
            DISTRICT BORDER
        =============================================== */}

        <GeoJSON
          data={districtGeoJson}

          style={{
            fillColor: '#f3f3ef',

            fillOpacity: 0.08,

            color: '#555752',

            weight: 2,
          }}
        />


        {/* ===============================================
            HEXES
        =============================================== */}

        {hexes.map((hex) => {
          const style =
            riskStyles[hex.risk]

          const isSelected =
            selectedHex?.id === hex.id


          return (
            <Polygon
              key={hex.id}

              positions={hex.positions}

              pathOptions={{
                ...style,

                weight: isSelected
                  ? 1.4
                  : style.weight,

                color: isSelected
                  ? '#3d3d39'
                  : style.color,
              }}

              eventHandlers={{
                mouseover: () => {
                  setHoveredHex(hex)
                },

                mouseout: () => {
                  setHoveredHex(null)
                },

                click: () => {
                  setSelectedHex(hex)
                },
              }}
            />
          )
        })}


        {/* ===============================================
            HOVER COPY
        =============================================== */}

        {hoverPolygon && (
          <GeoJSON
            key={`hover-${hoveredHex.id}`}

            data={hoverPolygon}

            interactive={false}

            style={{
              fillColor:
                riskStyles[
                  hoveredHex.risk
                ].fillColor,

              fillOpacity: 1,

              color: 'transparent',

              weight: 0,
            }}
          />
        )}

      </MapContainer>


      {/* =================================================
          HEADER
      ================================================= */}

      <div className="district-map-header">

        <div>
          <div className="district-map-kicker">
            МОСКВА / СВАО
          </div>

          <h2>
            Останкинский район
          </h2>
        </div>


        <div className="district-map-stats">

          <div>
            <span>
              Сот
            </span>

            <strong>
              {hexes.length}
            </strong>
          </div>


          <div>
            <span>
              Требуют внимания
            </span>

            <strong className="district-danger">
              {attentionCount}
            </strong>
          </div>

        </div>

      </div>


      {/* =================================================
          SELECTED HEX
      ================================================= */}

      {selectedHex && (
        <div className="district-selected">

          <div className="district-selected-label">
            ВЫБРАННАЯ СОТА
          </div>


          <div className="district-selected-id">
            {selectedHex.id}
          </div>


          <div
            className={
              `district-selected-risk ${selectedHex.risk}`
            }
          >
            {selectedHex.risk}
          </div>


          <button
            type="button"

            onClick={() =>
              setSelectedHex(null)
            }
          >
            Закрыть
          </button>

        </div>
      )}


      {/* =================================================
          LEGEND
      ================================================= */}

      <div className="district-map-legend">

        <span>
          <i className="map-dot normal" />
          Норма
        </span>


        <span>
          <i className="map-dot medium" />
          Средний
        </span>


        <span>
          <i className="map-dot high" />
          Высокий
        </span>


        <span>
          <i className="map-dot critical" />
          Критический
        </span>

      </div>

    </div>
  )
}


export default DistrictMap