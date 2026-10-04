import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { ArrowLeft, CheckCircle, Circle, Info, XCircle } from '@phosphor-icons/react';
import Departures from './components/Departures/Departures';
import Legal, { type LegalPage } from './components/Legal/Legal';
import { translate, useLang, type Lang } from './i18n';
import About from './components/About/About';
import MapView from './components/Map/MapView';
import Journeys from './components/Journeys/Journeys';
import Notice from './components/Notice/Notice';
import Reports from './components/Reports/Reports';
import SearchBar from './components/SearchBar/SearchBar';
import TripControls, { type TripTime } from './components/TripControls/TripControls';
import {
  api,
  formatTime,
  routeAffectedBy,
  type Disruption,
  type HealthResponse,
  type Place,
  type ReplanResponse,
  type RouteOption,
} from './services/api';
import { SELECTABLE_MODES, modeLabel } from './modes';
import './App.css';

const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low'];
const DISRUPTION_POLL_MS = 20_000;

type Tab = 'journey' | 'reports' | 'departures';

// Jev's reroute strategies, in plain words
const STRATEGY: Record<string, string> = {
  wait_it_out: 'wait it out',
  skip_segment: 'skip the broken part',
  full_reroute: 'replan the whole trip',
  switch_mode: 'switch mode',
  abort_and_return: 'turn back',
};

// ── The trip lives in the URL, so a reload or a shared link shows the same trip ──

function placeParam(p: Place): string {
  return `${p.lat.toFixed(5)},${p.lon.toFixed(5)},${p.name}`;
}

function parsePlace(value: string | null): Place | null {
  if (!value) return null;
  const [lat, lon, ...name] = value.split(',');
  const p = {
    name: name.join(',') || 'Dropped pin',
    lat: Number(lat),
    lon: Number(lon),
  };
  return Number.isFinite(p.lat) && Number.isFinite(p.lon) ? p : null;
}

function readUrl(): { from: Place | null; to: Place | null; time: TripTime } {
  const q = new URLSearchParams(window.location.search);
  const [kind, value = ''] = (q.get('when') ?? '').split(/:(.*)/s);
  const time: TripTime =
    kind === 'arrive'
      ? { mode: 'arrive', value }
      : kind === 'leave'
        ? { mode: 'depart', value }
        : { mode: 'depart', value: '' };
  return { from: parsePlace(q.get('from')), to: parsePlace(q.get('to')), time };
}

function writeUrl(from: Place | null, to: Place | null, time: TripTime): void {
  const q = new URLSearchParams();
  if (from) q.set('from', placeParam(from));
  if (to) q.set('to', placeParam(to));
  if (time.value) q.set('when', `${time.mode === 'arrive' ? 'arrive' : 'leave'}:${time.value}`);
  const query = q.toString();
  window.history.replaceState(null, '', query ? `?${query}` : window.location.pathname);
}

const INITIAL = readUrl();

/** The API calls the ends "Start" and "Destination"; show the names the traveller chose. */
function withPlaceNames(route: RouteOption, from: string, to: string): RouteOption {
  return {
    ...route,
    legs: route.legs.map((leg) => ({
      ...leg,
      from_name: leg.from_name === 'Start' ? from : leg.from_name,
      to_name: leg.to_name === 'Destination' ? to : leg.to_name,
    })),
  };
}

function StatusItem({ ok, label, title }: { ok: boolean | 'unknown'; label: string; title?: string }) {
  const { t } = useLang();
  const tone = ok === 'unknown' ? 'unknown' : ok ? 'ok' : 'off';
  const StatusIcon = tone === 'ok' ? CheckCircle : tone === 'off' ? XCircle : Circle;
  const state = t(tone === 'ok' ? 'working' : tone === 'off' ? 'not working' : 'not used yet');
  return (
    <span className={`status__item status__item--${tone}`} title={title}>
      <StatusIcon className={`status__icon--${tone}`} size={18} weight="fill" aria-hidden="true" />
      <span className="status__label">{t(label)}</span>
      <span className="sr-only">: {state}</span>
    </span>
  );
}

function App() {
  const [origin, setOrigin] = useState<Place | null>(INITIAL.from);
  const [destination, setDestination] = useState<Place | null>(INITIAL.to);
  const [time, setTime] = useState<TripTime>(INITIAL.time);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [legal, setLegal] = useState<LegalPage | null>(null);
  const [board, setBoard] = useState<Place | null>(null); // Stop shown on the departures board
  const { lang, setLang, t } = useLang();
  const langRef = useRef(lang); // Read by planTrip, which must stay stable
  useEffect(() => {
    langRef.current = lang;
  }, [lang]);
  const [modes, setModes] = useState<string[]>(SELECTABLE_MODES);
  const [routes, setRoutes] = useState<RouteOption[]>([]);
  const [selected, setSelected] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [disruptions, setDisruptions] = useState<Disruption[]>([]);
  const [dismissed, setDismissed] = useState<number | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [replan, setReplan] = useState<ReplanResponse | null>(null);
  const [replanSelected, setReplanSelected] = useState(0);
  const [tab, setTab] = useState<Tab>('journey');

  // ── Data loading ──

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  const refreshDisruptions = useCallback(() => {
    api
      .getDisruptions('active')
      .then(setDisruptions)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshDisruptions();
    const timer = setInterval(refreshDisruptions, DISRUPTION_POLL_MS);
    return () => clearInterval(timer);
  }, [refreshDisruptions]);

  const latestRequest = useRef(0);

  const planTrip = useCallback(async (from: Place, to: Place, when: TripTime, allowed: string[]) => {
    if (when.mode === 'arrive' && !when.value) return; // Needs a deadline first
    const request = ++latestRequest.current;
    setLoading(true);
    setError(null);
    setReplan(null);
    try {
      const response = await api.planRoute({
        origin_lat: from.lat,
        origin_lon: from.lon,
        destination_lat: to.lat,
        destination_lon: to.lon,
        departure_time: when.mode === 'depart' ? when.value || undefined : undefined,
        arrive_by: when.mode === 'arrive' ? when.value : undefined,
        preferred_modes: allowed.length === SELECTABLE_MODES.length ? undefined : [...allowed, 'walk'],
        lang: langRef.current,
      });
      if (request !== latestRequest.current) return; // A newer request superseded this one
      setRoutes(response.routes.map((route) => withPlaceNames(route, from.name, to.name)));
      setMessage(response.message ?? null);
      setSelected(0);
    } catch (err) {
      if (request === latestRequest.current) {
        setError(
          err instanceof Error
            ? err.message
            : translate(langRef.current, 'The server didn’t answer. Check that the backend is running.'),
        );
      }
    } finally {
      if (request === latestRequest.current) setLoading(false);
    }
  }, []);

  // Plan the trip from a shared link once (deferred, so a dev-mode double mount plans once)
  useEffect(() => {
    if (!INITIAL.from || !INITIAL.to) return;
    const id = setTimeout(() => planTrip(INITIAL.from!, INITIAL.to!, INITIAL.time, SELECTABLE_MODES));
    return () => clearTimeout(id);
  }, [planTrip]);

  useEffect(() => writeUrl(origin, destination, time), [origin, destination, time]);

  // ── Handlers ──

  /** Set start and/or destination; plan as soon as both are known. */
  const updateTrip = useCallback(
    (from: Place | null, to: Place | null) => {
      latestRequest.current++; // Ignore any plan still in flight for the old trip
      setLoading(false);
      setOrigin(from);
      setDestination(to);
      setRoutes([]);
      setReplan(null);
      setError(null);
      setMessage(null);
      setTab('journey');
      if (from && to) planTrip(from, to, time, modes);
    },
    [time, modes, planTrip],
  );

  const handleMapClick = useCallback(
    (lat: number, lon: number) => {
      const pin = {
        name: t('Dropped pin'),
        label: `${lat.toFixed(4)}, ${lon.toFixed(4)}`,
        lat,
        lon,
      };
      if (!origin || destination) updateTrip(pin, null);
      else updateTrip(origin, pin);
    },
    [origin, destination, updateTrip, t],
  );

  const handleTimeChange = useCallback(
    (value: TripTime) => {
      setTime(value);
      if (origin && destination) planTrip(origin, destination, value, modes);
    },
    [origin, destination, modes, planTrip],
  );

  const handleModesChange = useCallback(
    (value: string[]) => {
      setModes(value);
      if (origin && destination) planTrip(origin, destination, time, value);
    },
    [origin, destination, time, planTrip],
  );

  const handleSubmitDisruption = useCallback(
    async (text: string, source: string) => {
      await api.createDisruption(text, source);
      setDismissed(null);
      refreshDisruptions();
    },
    [refreshDisruptions],
  );

  const handleResolve = useCallback(
    (id: number) => {
      api
        .resolveDisruption(id)
        .then(refreshDisruptions)
        .catch(() => undefined);
    },
    [refreshDisruptions],
  );

  const selectedRoute = routes[selected] ?? null;
  // Stable map points: a new array each render would re-trigger the map's fly-to
  const originPoint = useMemo<[number, number] | null>(() => (origin ? [origin.lat, origin.lon] : null), [origin]);
  const destinationPoint = useMemo<[number, number] | null>(
    () => (destination ? [destination.lat, destination.lon] : null),
    [destination],
  );

  const handleReplan = useCallback(async () => {
    if (!selectedRoute) return;
    // Replan from where the traveller is now, or from the start if the trip hasn't begun
    const start = new Date(selectedRoute.departure_time).getTime();
    const at = new Date(Date.now() < start ? start - 60_000 : Date.now()).toISOString();
    setLoading(true);
    setTab('journey');
    try {
      const allowed = modes.length === SELECTABLE_MODES.length ? undefined : [...modes, 'walk'];
      const result = await api.replan(selectedRoute, at, allowed, lang);
      setReplan(result);
      setReplanSelected(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('Replanning failed. Try again.'));
    } finally {
      setLoading(false);
    }
  }, [selectedRoute, modes, lang, t]);

  /** Switch language; a shown trip is planned again so its messages switch too. */
  const switchLang = (next: Lang) => {
    setLang(next);
    langRef.current = next;
    if (origin && destination && !replan) planTrip(origin, destination, time, modes);
  };

  const showDepartures = useCallback((place: Place) => {
    setBoard(place);
    setTab('departures');
  }, []);

  // ── Notice: the selected trip's problem first, otherwise the most severe report ──

  const affecting =
    selectedRoute && !replan ? routeAffectedBy(selectedRoute, disruptions, health?.coverage) : undefined;
  const mostSevere = [...disruptions].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity || 'low') - SEVERITY_ORDER.indexOf(b.severity || 'low'),
  )[0];
  const noticeFor = affecting ?? mostSevere;
  const showNotice = noticeFor && noticeFor.id !== dismissed;

  const shownRoute = replan ? (replan.routes[replanSelected] ?? null) : selectedRoute;
  const decision = replan?.decision;
  let replanNote: string | null = null;
  if (decision) {
    const strategy = decision.strategy ?? '';
    replanNote = t('Jev advises: {strategy}', { strategy: t(STRATEGY[strategy] ?? strategy.replace(/_/g, ' ')) });
    if (decision.urgency != null) replanNote += t(', urgency {pct}%', { pct: Math.round(decision.urgency * 100) });
    if (decision.applied && decision.preferred_alternative_mode)
      replanNote += t('. {mode} options are listed first.', {
        mode: t(modeLabel(decision.preferred_alternative_mode)),
      });
    else replanNote += '.';
  } else if (replan && !replan.original_still_valid) {
    replanNote = replan.original_issues
      .filter((i) => i.severity === 'error')
      .map((i) => i.message)
      .join(' ');
  }

  return (
    <div className="app">
      <a className="skip-link" href="#planner">
        {t('Skip to trip planner')}
      </a>
      <header className="header">
        <div className="brand">
          <span className="brand__name">
            Fast<span>Mile</span>
          </span>
          <span className="brand__region">{t('Trips across Germany')}</span>
        </div>
        <div className="header__right">
          {health ? (
            <div className="status" aria-label={t('Service status')}>
              <StatusItem ok={health.network_loaded} label="Timetables" />
              <StatusItem
                ok={!health.llm ? false : health.llm.status === 'untested' ? 'unknown' : health.llm.status !== 'error'}
                label="Language model"
                title={
                  !health.llm
                    ? t('Off: notices are read by rules')
                    : health.llm.status === 'error'
                      ? t('Not working, notices are read by rules: {error}', { error: health.llm.last_error ?? '' })
                      : health.llm.status === 'untested' || health.llm.status === 'ready'
                        ? t(
                            health.llm.status === 'ready' ? '{model} (key works, not used yet)' : '{model} (not used yet)',
                            { model: health.llm.model },
                          )
                        : (health.llm.answered_by ?? health.llm.model)
                }
              />
              <StatusItem ok={health.jev_enabled} label="Jev" />
            </div>
          ) : (
            <div className="status">
              <StatusItem ok={false} label="Server offline" />
            </div>
          )}
          <div className="lang" role="group" aria-label="Language / Sprache">
            {(['de', 'en'] as const).map((code) => (
              <button
                key={code}
                type="button"
                className={`lang__option${lang === code ? ' lang__option--on' : ''}`}
                aria-pressed={lang === code}
                title={code === 'de' ? t('Switch to German') : t('Switch to English')}
                onClick={() => switchLang(code)}
              >
                {code.toUpperCase()}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setAboutOpen(true)}
            aria-label={t('How FastMile works')}
            title={t('How FastMile works')}
          >
            <Info size={18} aria-hidden="true" />
          </button>
        </div>
      </header>
      <About open={aboutOpen} onClose={() => setAboutOpen(false)} />
      <Legal page={legal} onClose={() => setLegal(null)} />

      <main className="main">
        <aside className="panel" id="planner" tabIndex={-1} aria-label={t('Trip planner')}>
          <SearchBar from={origin} to={destination} onChange={updateTrip} />
          <TripControls time={time} onTimeChange={handleTimeChange} modes={modes} onModesChange={handleModesChange} />
          <div className="tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'journey'}
              className={`tab${tab === 'journey' ? ' tab--on' : ''}`}
              onClick={() => setTab('journey')}
            >
              {t('Connections')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'reports'}
              className={`tab${tab === 'reports' ? ' tab--on' : ''}`}
              onClick={() => setTab('reports')}
            >
              {t('Disruptions')}
              {disruptions.length > 0 && <span className="tab__count">{disruptions.length}</span>}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'departures'}
              className={`tab${tab === 'departures' ? ' tab--on' : ''}`}
              onClick={() => setTab('departures')}
            >
              {t('Departures')}
            </button>
          </div>
          <div className="panel__scroll" role="tabpanel">
            {tab === 'journey' ? (
              <>
                {replan && (
                  <div className="journeys__note journeys__back">
                    <button type="button" className="btn btn--quiet btn--small" onClick={() => setReplan(null)}>
                      <ArrowLeft size={14} aria-hidden="true" />
                      {t('Back to all connections')}
                    </button>
                  </div>
                )}
                <Journeys
                  routes={replan ? replan.routes : routes}
                  selected={replan ? replanSelected : selected}
                  onSelect={replan ? setReplanSelected : setSelected}
                  loading={loading}
                  error={error}
                  hasTrip={Boolean(origin && destination)}
                  heading={
                    replan
                      ? replan.original_still_valid
                        ? t('Your trip still works')
                        : t('New connections from {place}, {time}', {
                            place: replan.position.name,
                            time: formatTime(replan.position.time),
                          })
                      : undefined
                  }
                  note={
                    replan
                      ? replan.original_still_valid
                        ? t('No active disruption affects it.')
                        : replanNote
                      : (message ??
                        (routes[0]?.source === 'transitous'
                          ? t('Outside the dashed FastMile region: connections from the Germany-wide timetable (Transitous).')
                          : null))
                  }
                  original={replan && !replan.original_still_valid ? selectedRoute : null}
                  cutAt={replan?.position.time}
                  destinationName={destination?.name}
                  onShowDepartures={showDepartures}
                />
              </>
            ) : tab === 'reports' ? (
              <Reports disruptions={disruptions} onSubmit={handleSubmitDisruption} onResolve={handleResolve} />
            ) : (
              <Departures place={board ?? origin} />
            )}
          </div>
          <footer className="panel__foot">
            <button type="button" className="link-btn" onClick={() => setLegal('imprint')}>
              {t('Imprint')}
            </button>
            <button type="button" className="link-btn" onClick={() => setLegal('privacy')}>
              {t('Privacy')}
            </button>
          </footer>
        </aside>

        <div className="map-area">
          <MapView
            onMapClick={handleMapClick}
            origin={originPoint}
            destination={destinationPoint}
            route={shownRoute}
            position={replan && !replan.arrived ? replan.position : null}
            attribution={
              health?.germany_routing
                ? `${health.data_attribution ?? 'DELFI e.V.'}, Transitous`
                : (health?.data_attribution ?? null)
            }
            coverage={health?.coverage ?? null}
            onStopClick={showDepartures}
          />
          {showNotice && (
            <Notice
              message={
                affecting
                  ? t('Your trip is affected: {summary}', { summary: affecting.summary || affecting.raw_text })
                  : noticeFor.summary || noticeFor.raw_text
              }
              actionLabel={affecting ? t('Find new connections') : undefined}
              onAction={affecting ? handleReplan : undefined}
              onDismiss={() => setDismissed(noticeFor.id)}
            />
          )}
          {!origin && <span className="map-hint">{t('Click the map to set a start, or search on the left')}</span>}
          {origin && !destination && <span className="map-hint">{t('Now click or search the destination')}</span>}
        </div>
      </main>
    </div>
  );
}

export default App;
