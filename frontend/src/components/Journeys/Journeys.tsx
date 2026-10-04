import { Signpost } from '@phosphor-icons/react';
import type { Issue, Place, RouteLeg, RouteOption } from '../../services/api';
import { formatTime } from '../../services/api';
import { MODE_ICON, isLight, legColor, modeLabel } from '../../modes';
import { useLang, type Translate } from '../../i18n';

interface JourneysProps {
  routes: RouteOption[];
  selected: number;
  onSelect: (index: number) => void;
  loading: boolean;
  error: string | null;
  heading?: string;
  note?: string | null;
  original?: RouteOption | null; // Shown greyed above replanned options
  cutAt?: string; // Where the original trip broke (the replan position time)
  destinationName?: string; // The place the traveller chose, shown at the end of each trip
  hasTrip: boolean;
  onShowDepartures?: (place: Place) => void; // Open the departures board for a boarding stop
}

const MIN = 60_000;
const ms = (iso: string) => new Date(iso).getTime();

function axisFor(routes: RouteOption[]): {
  start: number;
  end: number;
  ticks: number[];
} {
  const start = Math.min(...routes.map((r) => ms(r.departure_time)));
  const end = Math.max(...routes.map((r) => ms(r.arrival_time)));
  const span = Math.max(end - start, 15 * MIN);
  const step = span <= 90 * MIN ? 15 * MIN : span <= 180 * MIN ? 30 * MIN : 60 * MIN;
  const ticks: number[] = [];
  for (let t = Math.ceil(start / step) * step; t <= start + span; t += step) {
    const at = (t - start) / span;
    if (at > 0.06 && at < 0.94) ticks.push(t); // Centred labels at the very edge would overflow
  }
  return { start, end: start + span, ticks };
}

function changes(n: number, t: Translate): string {
  if (n === 0) return t('Direct');
  return n === 1 ? t('1 change') : t('{n} changes', { n });
}

function crowding(occ: number, t: Translate): string {
  if (occ >= 0.97) return t('likely full');
  if (occ >= 0.8) return t('crowded');
  if (occ >= 0.5) return t('busy');
  return t('seats free');
}

function distance(km: number): string {
  return km < 1 ? `${Math.round(km * 100) * 10} m` : `${km.toFixed(1)} km`;
}

function LiveDelay({ route }: { route: RouteOption }) {
  const { t } = useLang();
  const late = Math.max(0, ...route.legs.map((leg) => leg.delay_minutes ?? 0));
  if (route.legs.some((leg) => leg.cancelled))
    return <span className="journey__live journey__live--late">{t('Cancelled')}</span>;
  if (late === 0) return null;
  return <span className="journey__live journey__live--late">{t('Live: {n} min late', { n: late })}</span>;
}

function Reliability({ score }: { score: number }) {
  const { t } = useLang();
  const pct = Math.round(score * 100);
  const tone = score >= 0.8 ? '' : score >= 0.5 ? ' reliability--risky' : ' reliability--bad';
  return (
    <span className={`reliability${tone}`} title={t('Chance the whole chain works as planned')}>
      <span className="reliability__bar">
        <span className="reliability__fill" style={{ width: `${pct}%` }} />
      </span>
      {t('{pct}% likely to work', { pct })}
    </span>
  );
}

function Chain({ route, start, end, cutAt }: { route: RouteOption; start: number; end: number; cutAt?: number }) {
  const span = end - start;
  return (
    <span className="chain" aria-hidden="true">
      {route.legs.map((leg, i) => {
        const left = ((ms(leg.departure_time) - start) / span) * 100;
        const width = ((ms(leg.arrival_time) - ms(leg.departure_time)) / span) * 100;
        if (leg.mode === 'walk') {
          return (
            <span key={i} className="chain__leg chain__leg--walk" style={{ left: `${left}%`, width: `${width}%` }} />
          );
        }
        const color = legColor(leg);
        const label = leg.line_name || modeLabel(leg.mode);
        return (
          <span
            key={i}
            className={`chain__leg${isLight(color) ? ' chain__leg--light' : ''}`}
            style={{ left: `${left}%`, width: `${width}%`, background: color }}
          >
            {width >= 9 ? label : ''}
          </span>
        );
      })}
      {cutAt !== undefined && <span className="chain__cut" style={{ left: `${((cutAt - start) / span) * 100}%` }} />}
    </span>
  );
}

function LegIcon({ mode }: { mode: string }) {
  const ModeIcon = MODE_ICON[mode];
  return ModeIcon ? <ModeIcon className="timeline__icon" size={16} aria-hidden="true" /> : null;
}

function LegDescription({ leg, onShowDepartures }: { leg: RouteLeg; onShowDepartures?: (place: Place) => void }) {
  const { t } = useLang();
  const minutes = Math.max(1, Math.round(leg.duration_minutes));
  if (leg.stops.length > 0) {
    const color = legColor(leg);
    return (
      <>
        <div className="timeline__what">
          <span
            className="line-badge"
            style={{
              background: color,
              color: isLight(color) ? '#1b2430' : '#fff',
            }}
          >
            {leg.line_name || t(modeLabel(leg.mode))}
          </span>
          {leg.headsign ? t('towards {headsign}', { headsign: leg.headsign }) : t(modeLabel(leg.mode))}
        </div>
        <div className="timeline__detail">
          {t('From {stop}', { stop: leg.from_name })}
          {leg.platform && t(', platform {platform}', { platform: leg.platform })}.{' '}
          {leg.stops.length - 1 === 1
            ? t('Get off at {stop} after 1 stop, {min} min', { stop: leg.to_name, min: minutes })
            : t('Get off at {stop} after {n} stops, {min} min', {
                stop: leg.to_name,
                n: leg.stops.length - 1,
                min: minutes,
              })}
          {leg.occupancy != null && `, ${crowding(leg.occupancy, t)}`}.
        </div>
        {(leg.on_request || onShowDepartures) && (
          <div className="timeline__actions">
            {leg.on_request && <span className="timeline__booking">{t('Book by phone')}</span>}
            {onShowDepartures && (
              <button
                type="button"
                className="link-btn"
                onClick={() => onShowDepartures({ name: leg.from_name, lat: leg.from_lat, lon: leg.from_lon })}
              >
                <Signpost size={14} aria-hidden="true" />
                {t('Departures here')}
              </button>
            )}
          </div>
        )}
      </>
    );
  }
  if (leg.mode === 'walk') {
    return (
      <div className="timeline__detail">
        <LegIcon mode="walk" />
        {leg.distance_km >= 0.01
          ? t('Walk {min} min ({distance}) to {stop}', {
              min: minutes,
              distance: distance(leg.distance_km),
              stop: leg.to_name,
            })
          : t('Walk {min} min to {stop}', { min: minutes, stop: leg.to_name })}
      </div>
    );
  }
  if (leg.mode === 'on_demand') {
    return (
      <>
        <div className="timeline__what">
          <LegIcon mode="on_demand" />
          {t('On-demand shuttle to {stop}', { stop: leg.to_name })}
        </div>
        <div className="timeline__detail">
          {t('Book now: pickup in about {wait} min, {min} min in total', {
            wait: Math.round(leg.wait_minutes),
            min: minutes,
          })}
          {leg.provider && `, ${leg.provider}`}.
        </div>
      </>
    );
  }
  return (
    <>
      <div className="timeline__what">
        <LegIcon mode={leg.mode} />
        {t('{mode} to {stop}', { mode: t(modeLabel(leg.mode)), stop: leg.to_name })}
      </div>
      <div className="timeline__detail">
        {t('Pick up at {stop}', { stop: leg.from_name })}
        {leg.vehicles_available != null && t(' ({n} available now)', { n: leg.vehicles_available })}, {minutes} min,{' '}
        {distance(leg.distance_km)}
        {leg.provider && `, ${leg.provider}`}.
      </div>
    </>
  );
}

/** Live delay under the departure time: "+4" late, "on time" when live data says so. */
function Delay({ leg }: { leg: RouteLeg }) {
  const { t } = useLang();
  if (leg.cancelled) return <span className="delay delay--late">{t('cancelled')}</span>;
  if (leg.delay_minutes == null || leg.stops.length === 0) return null;
  if (leg.delay_minutes <= 0) return <span className="delay delay--ok">{t('on time')}</span>;
  return (
    <span className="delay delay--late">
      +{leg.delay_minutes}
      <span className="sr-only"> {t('min late')}</span>
    </span>
  );
}

function Timeline({
  route,
  destinationName,
  onShowDepartures,
}: {
  route: RouteOption;
  destinationName?: string;
  onShowDepartures?: (place: Place) => void;
}) {
  const last = route.legs[route.legs.length - 1];
  return (
    <ol className="timeline">
      {route.legs.map((leg, i) => (
        <li
          key={i}
          className={`timeline__leg${leg.mode === 'walk' ? ' timeline__leg--walk' : ''}`}
          style={{ '--leg-color': legColor(leg) } as React.CSSProperties}
        >
          <span className="timeline__time tnum">
            {formatTime(leg.departure_time)}
            <Delay leg={leg} />
          </span>
          <span className="timeline__rail" />
          <div className="timeline__body">
            <LegDescription leg={leg} onShowDepartures={onShowDepartures} />
          </div>
        </li>
      ))}
      <li className="timeline__end">
        <span className="timeline__time tnum">{formatTime(last.arrival_time)}</span>
        <span className="timeline__rail" />
        <span>{destinationName ?? last.to_name}</span>
      </li>
    </ol>
  );
}

function Notes({ issues, all }: { issues: Issue[]; all: boolean }) {
  const shown = all ? issues : issues.filter((i) => i.severity === 'error');
  if (shown.length === 0) return null;
  return (
    <ul className="notes">
      {shown.map((issue, i) => (
        <li key={i} className={`note note--${issue.severity}`}>
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

export default function Journeys({
  routes,
  selected,
  onSelect,
  loading,
  error,
  heading,
  note,
  original,
  cutAt,
  destinationName,
  hasTrip,
  onShowDepartures,
}: JourneysProps) {
  const { t } = useLang();
  if (error) {
    return (
      <div className="state state--error" role="alert">
        <h3>{t('Couldn’t plan this trip')}</h3>
        {error}
      </div>
    );
  }
  if (!hasTrip) {
    return (
      <div className="state">
        <h3>{t('Where to?')}</h3>
        {t(
          'Search for a start and destination above, or click two points on the map. FastMile combines buses, trains, bikes and on-demand rides, and warns you when a disruption breaks your trip.',
        )}
      </div>
    );
  }
  if (loading) {
    return (
      <div className="journeys" aria-busy="true">
        <p className="journeys__heading" role="status">
          {t('Finding connections')}
        </p>
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton" aria-hidden="true">
            <span className="skeleton__line skeleton__line--times" />
            <span className="skeleton__line skeleton__line--chain" />
            <span className="skeleton__line skeleton__line--meta" />
          </div>
        ))}
      </div>
    );
  }
  if (routes.length === 0) {
    return (
      <div className="state">
        <h3>{heading ?? t('No connections found')}</h3>
        {note ?? t('Try a later time, or allow more modes.')}
      </div>
    );
  }

  const axis = axisFor(original ? [original, ...routes] : routes);
  return (
    <div className="journeys">
      {heading && <h2 className="journeys__heading">{heading}</h2>}
      {note && <p className="journeys__note">{note}</p>}
      <div className="axis tnum" aria-hidden="true">
        {axis.ticks.map((t) => (
          <span
            key={t}
            className="axis__tick"
            style={{
              left: `${((t - axis.start) / (axis.end - axis.start)) * 100}%`,
            }}
          >
            {formatTime(new Date(t).toISOString())}
          </span>
        ))}
      </div>
      {original && (
        <div className="journey journey--original">
          <span className="journey__top">
            <span className="journey__times tnum">
              {formatTime(original.departure_time)} – {formatTime(original.arrival_time)}
            </span>
            <span className="journey__tag journey__tag--muted">{t('Your planned trip')}</span>
          </span>
          <Chain route={original} start={axis.start} end={axis.end} cutAt={cutAt ? ms(cutAt) : undefined} />
        </div>
      )}
      {routes.map((route, index) => {
        const isSelected = index === selected;
        return (
          <div
            key={`${route.rank}-${route.departure_time}`}
            className={`journey${isSelected ? ' journey--selected' : ''}${route.feasible ? '' : ' journey--infeasible'}`}
          >
            <button
              type="button"
              className="journey__select"
              aria-expanded={isSelected}
              onClick={() => onSelect(index)}
            >
              <span className="journey__top">
                <span className="journey__times tnum">
                  {formatTime(route.departure_time)} – {formatTime(route.arrival_time)}
                </span>
                <span className="journey__duration tnum">
                  {t('{n} min', { n: Math.round(route.total_duration_minutes) })}
                </span>
                {route.label && <span className="journey__tag">{t(route.label)}</span>}
              </span>
              <Chain route={route} start={axis.start} end={axis.end} />
              <span className="journey__meta">
                <span>{changes(route.transfers, t)}</span>
                {(route.transfers > 0 || route.reliability_score < 0.995) && (
                  <Reliability score={route.reliability_score} />
                )}
                {route.jev_checked_transfers > 0 && <span>{t('Transfers checked by Jev')}</span>}
                <LiveDelay route={route} />
              </span>
            </button>
            <Notes issues={route.issues} all={isSelected} />
            {isSelected && (
              <Timeline route={route} destinationName={destinationName} onShowDepartures={onShowDepartures} />
            )}
          </div>
        );
      })}
    </div>
  );
}
