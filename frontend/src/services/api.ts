/**
 * FastMile API client — communicates with the FastAPI backend.
 */

import type { Lang } from '../i18n';

// Empty = same origin (Vite proxies /api to the backend). Set VITE_API_URL to call a backend elsewhere.
const API_BASE = import.meta.env.VITE_API_URL || '';

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  jev_enabled: boolean;
  llm: LlmState | null;
  network_loaded: boolean;
  data_attribution: string | null;
  coverage: Bounds | null; // Where FastMile's own timetables apply
  germany_routing: boolean; // Trips elsewhere in Germany go through Transitous
}

/** [south, west, north, east] */
export type Bounds = [number, number, number, number];

interface ServiceState {
  status?: string;
  last_error?: string | null;
}

/** One language-model client, or the whole chain (then `chain` lists its links). */
export interface LlmState {
  provider: string;
  model: string;
  status: 'ok' | 'ready' | 'error' | 'untested'; // ready: key and models checked at startup
  last_error?: string;
  answered_by?: string; // Model that read the last report
  calls_today?: string; // "12 of 200", for capped paid models
  quota_exhausted_until?: string;
  chain?: LlmState[];
}

export interface NetworkStatus {
  gtfs_source: string | null;
  stops: number;
  routes: number;
  trips_today: number;
  shared_vehicles: Record<string, number>;
  realtime: {
    delayed_trips: number;
    canceled_trips: number;
    error: string | null;
  } | null;
  models: Record<string, { trained: boolean; trained_rows: number }>;
  llm: LlmState | null;
  jev_enabled: boolean;
  street_routing: {
    available: boolean;
    calls: number;
    failures: number;
  } | null;
  germany_routing: (ServiceState & { url: string; requests: number }) | null;
}

/** A searched or picked location. */
export interface Place {
  name: string;
  label?: string;
  lat: number;
  lon: number;
  kind?: string;
}

export interface StopCall {
  stop_id: string;
  name: string;
  lat: number;
  lon: number;
  time: string;
}

export interface Issue {
  severity: 'info' | 'warning' | 'error';
  message: string;
  leg_index: number | null;
}

export interface RouteLeg {
  mode: string;
  from_name: string;
  to_name: string;
  from_lat: number;
  from_lon: number;
  to_lat: number;
  to_lon: number;
  departure_time: string;
  arrival_time: string;
  duration_minutes: number;
  distance_km: number;
  line_name?: string | null;
  line_color?: string | null;
  route_id?: string | null;
  trip_id?: string | null;
  headsign?: string | null;
  provider?: string | null;
  vehicles_available?: number | null;
  wait_minutes: number;
  occupancy?: number | null;
  delay_minutes?: number | null; // Live delay at departure; null = no live data
  platform?: string | null;
  cancelled?: boolean;
  on_request?: boolean; // Runs only when booked by phone
  source?: string | null;
  instructions?: string | null;
  stops: StopCall[];
  path: [number, number][];
}

export interface RouteOption {
  rank: number;
  label?: string | null;
  departure_time: string;
  arrival_time: string;
  total_duration_minutes: number;
  total_distance_km: number;
  transfers: number;
  reliability_score: number;
  feasible: boolean;
  cost: number;
  issues: Issue[];
  jev_checked_transfers: number;
  source?: 'fastmile' | 'transitous';
  legs: RouteLeg[];
}

export interface RouteResponse {
  routes: RouteOption[];
  computed_at: string;
  disruptions_considered: number[];
  message?: string | null;
}

export interface RouteRequest {
  origin_lat: number;
  origin_lon: number;
  destination_lat: number;
  destination_lon: number;
  departure_time?: string;
  arrive_by?: string;
  preferred_modes?: string[];
  lang?: Lang;
}

export interface Departure {
  time: string; // Expected, live delay included
  scheduled_time: string;
  delay_minutes: number | null; // null = no live data
  line_name: string;
  line_color: string | null;
  mode: string;
  headsign: string;
  platform: string | null;
  cancelled: boolean;
  on_request: boolean; // Book by phone
  disruption_id: number | null;
  disruption: string | null;
}

export interface DeparturesResponse {
  stop: { name: string; lat: number; lon: number };
  source: 'fastmile' | 'transitous';
  departures: Departure[];
  computed_at: string;
}

export interface Operator {
  name: string;
  address: string;
  email: string;
  phone: string;
}

export interface LegalResponse {
  operator: Operator | null; // null until OPERATOR_* is set in .env
  services: string[];
}

export interface Disruption {
  id: number;
  raw_text: string;
  summary?: string | null;
  severity?: string | null;
  effect?: string | null;
  disruption_type?: string | null;
  delay_minutes?: number | null;
  needs_reroute?: number | null;
  extraction_method?: string | null;
  affected_lines: string[];
  affected_routes: string[];
  affected_stops: string[];
  alternatives?: string[]; // What the report recommends instead (language model)
  alternative_lines?: string[];
  start_time?: string | null;
  end_time?: string | null;
  source: string;
  confidence?: number | null;
  status: string;
  created_at: string;
}

export interface ReplanResponse {
  position: { name: string; lat: number; lon: number; time: string };
  arrived: boolean;
  original_still_valid: boolean;
  original_issues: Issue[];
  decision: {
    strategy: string | null;
    strategy_confidence: number | null;
    urgency: number | null;
    preferred_alternative_mode: string | null;
    mode_confidence: number | null;
    applied: boolean;
    model: string;
  } | null;
  routes: RouteOption[];
  computed_at: string;
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // Body wasn't JSON; keep the status text
    }
    throw new Error(`${response.status}: ${detail}`);
  }
  return response.json();
}

export const api = {
  health: () => apiFetch<HealthResponse>('/health'),

  networkStatus: () => apiFetch<NetworkStatus>('/network/status'),

  planRoute: (request: RouteRequest) =>
    apiFetch<RouteResponse>('/routes/plan', {
      method: 'POST',
      body: JSON.stringify(request),
    }),

  replan: (route: RouteOption, atTime?: string, preferredModes?: string[], lang: Lang = 'en') =>
    apiFetch<ReplanResponse>('/routes/replan', {
      method: 'POST',
      body: JSON.stringify({ route, at_time: atTime, preferred_modes: preferredModes, lang }),
    }),

  departures: (lat: number, lon: number, lang: Lang = 'en') =>
    apiFetch<DeparturesResponse>(`/departures?lat=${lat}&lon=${lon}&n=14&lang=${lang}`),

  legal: () => apiFetch<LegalResponse>('/legal'),

  getDisruptions: (status?: string) => apiFetch<Disruption[]>(`/disruptions${status ? `?status=${status}` : ''}`),

  createDisruption: (rawText: string, source: string) =>
    apiFetch<Disruption>('/disruptions', {
      method: 'POST',
      body: JSON.stringify({ raw_text: rawText, source }),
    }),

  resolveDisruption: (id: number) =>
    apiFetch<Disruption>(`/disruptions/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'resolved' }),
    }),

  geocode: (query: string) =>
    apiFetch<{ results: Place[] }>(`/geocode?q=${encodeURIComponent(query)}`).then((r) => r.results),
};

/** 'RE 10' / 're10' / 'Linie 830' → 'RE10' / 'RE10' / '830' (same rule as the backend). */
function normalizeLine(name: string): string {
  return name
    .trim()
    .replace(/^(buslinie|linie|line|bus|tram)\s*/i, '')
    .replace(/[\s\-_]/g, '')
    .toUpperCase();
}

function inBounds(lat: number, lon: number, b: Bounds | null | undefined): boolean {
  return !!b && lat >= b[0] && lat <= b[2] && lon >= b[1] && lon <= b[3];
}

/**
 * Does an active disruption break this route? Mirrors the backend blocking policy:
 * suspended lines block the line; skipped or closed stops block boarding/alighting there.
 * Legs planned by Transitous carry another feed's IDs, so they match by line name inside
 * the region FastMile has timetables for.
 */
export function routeAffectedBy(
  route: RouteOption,
  disruptions: Disruption[],
  coverage?: Bounds | null,
): Disruption | undefined {
  return disruptions.find((d) => {
    if (d.status !== 'active') return false;
    const blocksLines = d.effect === 'suspended' || d.effect === 'other';
    const blocksStops =
      d.effect === 'stops_not_served' ||
      d.effect === 'station_closed' ||
      (blocksLines && d.affected_routes.length === 0);
    // Times are local wall-clock strings; an open end means "until resolved"
    const start = d.start_time ? new Date(d.start_time).getTime() : -Infinity;
    const end = d.end_time ? new Date(d.end_time).getTime() : Infinity;
    return route.legs.some((leg) => {
      const overlaps = new Date(leg.departure_time).getTime() <= end && new Date(leg.arrival_time).getTime() >= start;
      if (!overlaps) return false;
      if (leg.source === 'transitous') {
        return (
          blocksLines &&
          d.affected_routes.length > 0 &&
          !!leg.line_name &&
          d.affected_lines.map(normalizeLine).includes(normalizeLine(leg.line_name)) &&
          leg.stops.some((s) => inBounds(s.lat, s.lon, coverage))
        );
      }
      if (blocksLines && leg.route_id && d.affected_routes.includes(leg.route_id)) return true;
      const ends = [leg.stops[0], leg.stops[leg.stops.length - 1]].filter(Boolean);
      return blocksStops && ends.some((s) => d.affected_stops.includes(s.stop_id));
    });
  });
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('de-DE', {
    hour: '2-digit',
    minute: '2-digit',
  });
}
