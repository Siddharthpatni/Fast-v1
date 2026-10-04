import { useEffect, useState } from 'react';
import { Phone, Warning } from '@phosphor-icons/react';
import { api, formatTime, type DeparturesResponse, type Place } from '../../services/api';
import { isLight } from '../../modes';
import { useLang } from '../../i18n';

const REFRESH_MS = 30_000;

/** Live departures, styled after a German station display: blue board, changes in yellow. */
export default function Departures({ place }: { place: Place | null }) {
  const { lang, t } = useLang();
  const [board, setBoard] = useState<DeparturesResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    if (!place) return;
    let cancelled = false;
    const load = () =>
      api
        .departures(place.lat, place.lon, lang)
        .then((b) => {
          if (cancelled) return;
          setBoard(b);
          setFailed(false);
        })
        .catch(() => !cancelled && setFailed(true));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [place, lang]);

  useEffect(() => {
    const clock = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(clock);
  }, []);

  if (!place) return <p className="state">{t('Pick a stop: search a start above, or click a stop on the map.')}</p>;

  // The board for another place is stale; show it only for this one
  const shown =
    board && Math.abs(board.stop.lat - place.lat) < 0.02 && Math.abs(board.stop.lon - place.lon) < 0.02 ? board : null;
  const notices = shown ? [...new Set(shown.departures.map((d) => d.disruption).filter((d): d is string => !!d))] : [];

  return (
    <section className="board" aria-label={t('Departures from {stop}', { stop: shown?.stop.name ?? place.name })}>
      <header className="board__head">
        <div>
          <p className="board__kicker">{t('Departures')}</p>
          <h2 className="board__stop">{shown?.stop.name ?? place.name}</h2>
        </div>
        <time className="board__clock tnum" dateTime={now.toISOString()}>
          {formatTime(now.toISOString())}
        </time>
      </header>

      {failed && !shown && <p className="board__state">{t('Couldn’t load departures')}</p>}
      {!failed && !shown && (
        <p className="board__state" role="status">
          {t('Loading departures')}
        </p>
      )}
      {shown && shown.departures.length === 0 && (
        <p className="board__state">{t('No departures in the next hours.')}</p>
      )}

      {shown && shown.departures.length > 0 && (
        <table className="board__table">
          <thead>
            <tr>
              <th scope="col">{t('Time')}</th>
              <th scope="col">{t('Line')}</th>
              <th scope="col">{t('Destination')}</th>
              <th scope="col" className="board__platform">
                {t('Platform')}
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.departures.map((d, i) => {
              const color = d.line_color || '#ffffff';
              return (
                <tr key={`${d.time}-${d.line_name}-${i}`} className={d.cancelled ? 'board__row--cancelled' : undefined}>
                  <td className="board__time tnum">
                    <span className="board__scheduled">{formatTime(d.scheduled_time)}</span>
                    {d.cancelled ? (
                      <span className="board__change">{t('cancelled')}</span>
                    ) : (
                      d.delay_minutes != null &&
                      d.delay_minutes > 0 && (
                        <span className="board__change">
                          +{d.delay_minutes}
                          <span className="sr-only"> {t('min late')}</span>
                        </span>
                      )
                    )}
                  </td>
                  <td>
                    <span
                      className="board__line"
                      style={{ background: color, color: isLight(color) ? '#0d1f5c' : '#ffffff' }}
                    >
                      {d.line_name}
                    </span>
                  </td>
                  <td className="board__dest">
                    {d.headsign}
                    {d.on_request && (
                      <span className="board__flag">
                        <Phone size={14} weight="bold" aria-hidden="true" />
                        {t('Phone to book')}
                      </span>
                    )}
                    {d.disruption && (
                      <span className="board__flag board__flag--disruption" title={d.disruption}>
                        <Warning size={14} weight="fill" aria-hidden="true" />
                        {t('Disruption')}
                      </span>
                    )}
                  </td>
                  <td className="board__platform tnum">{d.platform ?? ''}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {notices.length > 0 && (
        <div className="board__ticker" role="note">
          <span className="board__ticker-label">{t('Info:')}</span>
          <span className="board__ticker-window">
            <span className="board__ticker-text">{notices.join('  +++  ')}</span>
          </span>
        </div>
      )}

      {shown && (
        <p className="board__foot">
          {shown.source === 'fastmile' ? t('FastMile timetable') : t('Germany-wide timetable')}.{' '}
          {t('Updated {time}', { time: formatTime(shown.computed_at) })}
        </p>
      )}
    </section>
  );
}
