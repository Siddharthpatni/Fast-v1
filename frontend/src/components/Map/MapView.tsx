import { useEffect } from 'react';
import { CircleMarker, MapContainer, Marker, Polyline, Rectangle, Tooltip, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

import type { Bounds, Place, RouteOption } from '../../services/api';
import { formatTime } from '../../services/api';
import { legColor, modeLabel } from '../../modes';
import { useLang } from '../../i18n';
import Basemap from './Basemap';

interface MapProps {
  onMapClick: (lat: number, lon: number) => void;
  origin: [number, number] | null;
  destination: [number, number] | null;
  route: RouteOption | null;
  position: { name: string; lat: number; lon: number } | null;
  attribution: string | null; // Credit required by the transit data licence
  coverage: Bounds | null; // Where FastMile's own timetables apply
  onStopClick?: (stop: Place) => void; // Open the departures board for a stop on the route
}

// basemap.de covers Germany only; keep the view there
const GERMANY_CENTER: [number, number] = [51.16, 10.45];
const GERMANY_LIMITS = L.latLngBounds([46.8, 4.8], [55.6, 16.2]);

const pin = (variant: string) =>
  L.divIcon({
    className: `pin pin--${variant}`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
const startIcon = pin('from');
const endIcon = pin('to');

function ClickHandler({ onMapClick }: { onMapClick: (lat: number, lon: number) => void }) {
  useMapEvents({
    click(e) {
      onMapClick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

/** Fly to a searched place when it is set. */
function FlyTo({ point }: { point: [number, number] | null }) {
  const map = useMap();
  useEffect(() => {
    if (point) map.flyTo(point, Math.max(map.getZoom(), 13), { duration: 0.6 });
  }, [point, map]);
  return null;
}

/** Zoom to the selected route whenever it changes. */
function FitToRoute({ route }: { route: RouteOption | null }) {
  const map = useMap();
  useEffect(() => {
    if (!route) return;
    const points = route.legs.flatMap((leg) => leg.path);
    if (points.length > 1) map.fitBounds(L.latLngBounds(points), { padding: [48, 48] });
  }, [route, map]);
  return null;
}

export default function MapView({
  onMapClick,
  origin,
  destination,
  route,
  position,
  attribution,
  coverage,
  onStopClick,
}: MapProps) {
  const { t } = useLang();
  return (
    <MapContainer
      center={GERMANY_CENTER}
      zoom={6}
      minZoom={5}
      maxZoom={18}
      maxBounds={GERMANY_LIMITS}
      maxBoundsViscosity={0.8}
      style={{ height: '100%', width: '100%' }}
    >
      <Basemap attribution={attribution} />
      {coverage && (
        <Rectangle
          bounds={[
            [coverage[0], coverage[1]],
            [coverage[2], coverage[3]],
          ]}
          pathOptions={{
            className: 'coverage',
            weight: 1.5,
            dashArray: '6 6',
            fillOpacity: 0.04,
          }}
          interactive={false}
        />
      )}
      <ClickHandler onMapClick={onMapClick} />
      <FitToRoute route={route} />
      <FlyTo point={route ? null : (destination ?? origin)} />

      {route?.legs.map((leg, i) => {
        const isWalk = leg.mode === 'walk';
        return (
          <Polyline
            key={`casing-${i}`}
            positions={leg.path}
            pathOptions={{
              className: 'route-casing',
              weight: isWalk ? 7 : 10,
              opacity: 0.9,
            }}
          />
        );
      })}
      {route?.legs.map((leg, i) => {
        const isWalk = leg.mode === 'walk';
        return (
          <Polyline
            key={`leg-${i}`}
            positions={leg.path}
            pathOptions={{
              color: legColor(leg),
              weight: isWalk ? 4 : 6,
              opacity: 1,
              dashArray: isWalk ? '2 8' : undefined,
              lineCap: 'round',
            }}
          >
            <Tooltip sticky>
              {leg.line_name || t(modeLabel(leg.mode))}, {formatTime(leg.departure_time)}–{formatTime(leg.arrival_time)}
            </Tooltip>
          </Polyline>
        );
      })}
      {route?.legs.flatMap((leg, i) =>
        leg.stops.map((s, j) => {
          const end = j === 0 || j === leg.stops.length - 1;
          return (
            <CircleMarker
              key={`stop-${i}-${j}`}
              center={[s.lat, s.lon]}
              radius={end ? 6 : 4}
              bubblingMouseEvents={false} // A click opens departures; it must not also drop a pin
              eventHandlers={
                onStopClick ? { click: () => onStopClick({ name: s.name, lat: s.lat, lon: s.lon }) } : undefined
              }
              pathOptions={{
                className: 'route-stop',
                color: legColor(leg),
                weight: 2,
                fillOpacity: 1,
              }}
            >
              <Tooltip>
                {s.name}, {formatTime(s.time)}
                {onStopClick && (
                  <>
                    <br />
                    {t('Click for departures')}
                  </>
                )}
              </Tooltip>
            </CircleMarker>
          );
        }),
      )}

      {origin && <Marker position={origin} icon={startIcon} title={t('Start')} />}
      {destination && <Marker position={destination} icon={endIcon} title={t('Destination')} />}
      {position && (
        <CircleMarker
          center={[position.lat, position.lon]}
          radius={11}
          pathOptions={{
            className: 'route-position',
            weight: 3,
            fillOpacity: 1,
          }}
        >
          <Tooltip permanent direction="top">
            {t('You are here: {name}', { name: position.name })}
          </Tooltip>
        </CircleMarker>
      )}
    </MapContainer>
  );
}
