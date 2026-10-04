import { useEffect, useState } from 'react';
import { TileLayer, useMap } from 'react-leaflet';
import type { Layer } from 'leaflet';

/* basemap.de (BKG, CC BY 4.0): official vector styles, grey for light mode and dark for dark mode.
   The raster service only has colour and grey, so it is the fallback when WebGL is missing. */
const VECTOR_STYLES = 'https://sgx.geodatenzentrum.de/gdz_basemapde_vektor/styles';
const RASTER_GREY =
  'https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_grau/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png';
const CREDIT = 'Map: &copy; <a href="https://basemap.de">basemap.de / BKG</a> (CC BY 4.0)';

const darkQuery = '(prefers-color-scheme: dark)';

function useDarkScheme(): boolean {
  const [dark, setDark] = useState(() => window.matchMedia(darkQuery).matches);
  useEffect(() => {
    const media = window.matchMedia(darkQuery);
    const onChange = (e: MediaQueryListEvent) => setDark(e.matches);
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }, []);
  return dark;
}

function hasWebGL(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

export default function Basemap({ attribution }: { attribution: string | null }) {
  const map = useMap();
  const dark = useDarkScheme();
  const [raster, setRaster] = useState(() => !hasWebGL());
  const transit = attribution ? `Transit data: ${attribution}` : null;
  const credit = transit ? `${CREDIT} | ${transit}` : CREDIT;

  useEffect(() => {
    if (raster) return;
    let layer: Layer | null = null;
    let cancelled = false;
    // MapLibre is large, so it loads only when the map needs it
    Promise.all([
      import('maplibre-gl'),
      // MapLibre finds its worker next to its own module, which Vite's pre-bundling moves,
      // so Vite bundles the worker and MapLibre is told where it is
      import('maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'),
      import('@maplibre/maplibre-gl-leaflet'),
      import('maplibre-gl/dist/maplibre-gl.css'),
    ])
      .then(([maplibre, worker, binding]) => {
        if (cancelled) return;
        maplibre.setWorkerUrl(worker.default);
        const gl = binding.maplibreGL({ style: `${VECTOR_STYLES}/${dark ? 'bm_web_drk' : 'bm_web_gry'}.json` });
        gl.addTo(map);
        // A listener stops MapLibre logging every failed tile as a console error
        gl.getMaplibreMap().on('error', (e) => console.warn('basemap.de:', e.error?.message ?? e));
        layer = gl;
      })
      .catch((err) => {
        console.warn('Vector basemap unavailable, using raster tiles:', err);
        if (!cancelled) setRaster(true);
      });
    return () => {
      cancelled = true;
      layer?.remove();
    };
  }, [map, dark, raster]);

  useEffect(() => {
    // The vector style brings basemap.de's own credit; the raster TileLayer carries its own
    if (raster || !transit) return;
    map.attributionControl.addAttribution(transit);
    return () => {
      map.attributionControl.removeAttribution(transit);
    };
  }, [map, transit, raster]);

  return raster ? (
    <TileLayer key={credit} className="basemap-raster" attribution={credit} url={RASTER_GREY} maxZoom={18} />
  ) : null;
}
