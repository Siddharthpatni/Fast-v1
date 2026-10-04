import { useEffect, useRef, useState } from 'react';
import { X } from '@phosphor-icons/react';
import { api, type NetworkStatus } from '../../services/api';
import { useLang } from '../../i18n';

interface AboutProps {
  open: boolean;
  onClose: () => void;
}

/** What plans the trips, which models run, and how they are doing. Live from /network/status. */
export default function About({ open, onClose }: AboutProps) {
  const { lang, t } = useLang();
  const de = lang === 'de';
  const dialog = useRef<HTMLDialogElement>(null);
  const [status, setStatus] = useState<NetworkStatus | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (open && !el.open) {
      el.showModal();
      api
        .networkStatus()
        .then((s) => {
          setStatus(s);
          setFailed(false);
        })
        .catch(() => setFailed(true));
    } else if (!open && el.open) {
      el.close();
    }
  }, [open]);

  const state = (s: { status?: string; last_error?: string | null } | null | undefined): string => {
    if (!s) return de ? 'aus' : 'off';
    if (s.status === 'error') return `${t('not working')}${s.last_error ? ` (${s.last_error})` : ''}`;
    return t(s.status === 'untested' ? 'not used yet' : s.status === 'ready' ? 'key works, not used yet' : 'working');
  };
  const trained = (name: string) =>
    status?.models[name]?.trained
      ? de
        ? `trainiertes Modell (${status.models[name].trained_rows} Zeilen)`
        : `trained model (${status.models[name].trained_rows} rows)`
      : de
        ? 'Faustregel, noch keine Trainingsdaten'
        : 'rule of thumb, no training data yet';
  const chain = status?.llm?.chain ?? (status?.llm ? [status.llm] : []);
  const models = chain.flatMap((link) =>
    link.model.split(',').map((m) => ({
      model: m.trim(),
      paid: link.provider === 'openrouter' && !m.includes(':free'),
      link,
    })),
  );

  return (
    <dialog ref={dialog} className="about" aria-labelledby="about-title" onClose={onClose}>
      <div className="about__head">
        <h2 id="about-title">{t('How FastMile works')}</h2>
        <button type="button" className="icon-btn" onClick={onClose} aria-label={t('Close')}>
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      {failed && <p className="about__error">{t('The server didn’t answer, so live status is missing.')}</p>}
      <dl className="about__list">
        <dt>{de
            ? 'Fahrten in der FastMile-Region (gestrichelt: Niedersachsen und Umgebung)'
            : 'Trips in the FastMile region (dashed: Lower Saxony and around)'}</dt>
        <dd>
          {de
            ? 'FastMiles eigener Router (Connection Scan Algorithm, kein KI-Modell) auf dem Regionalfahrplan'
            : "FastMile's own router (Connection Scan Algorithm, not an AI model) on the regional timetable"}
          {status &&
            (de
              ? ` (${status.stops.toLocaleString('de-DE')} Haltestellen, heute ${status.trips_today.toLocaleString('de-DE')} Fahrten)`
              : ` (${status.stops.toLocaleString('de-DE')} stops, ${status.trips_today.toLocaleString('de-DE')} trips today)`)}
          .{' '}
          {de
            ? 'Gemeldete Störungen sperren Linien und Haltestellen schon vor der Suche; Live-Verspätungen kommen aus dem GTFS-RT-Feed. Rufbusse (AST) erkennt FastMile an der Fahrplanangabe „telefonisch anmelden“.'
            : 'Reported disruptions block lines and stops before the search; live delays come from the GTFS-RT feed. Call-ahead buses (AST) are recognised from the timetable’s “phone to book” flag.'}
        </dd>

        <dt>{de ? 'Fahrten im übrigen Deutschland' : 'Trips elsewhere in Germany'}</dt>
        <dd>
          {de
            ? 'Transitous, ein freier Gemeinschafts-Routenplaner (MOTIS) mit dem bundesweiten DELFI-Fahrplan und Live-Verspätungen: '
            : 'Transitous, a free community route planner (MOTIS) with the national DELFI timetable and live delays: '}
          {state(status?.germany_routing)}.{' '}
          {de
            ? 'FastMile prüft auch diese Verbindungen gegen gemeldete Störungen.'
            : 'FastMile checks its results against reported disruptions too.'}
        </dd>

        <dt>{de ? 'Fuß-, Rad- und Autowege' : 'Walking, cycling and driving legs'}</dt>
        <dd>
          {de ? 'OSRM auf den FOSSGIS-Servern (OpenStreetMap): ' : 'OSRM on the FOSSGIS servers (OpenStreetMap): '}
          {t(status?.street_routing?.available === false ? 'not working' : 'working')}.
        </dd>

        <dt>{de ? 'Adresssuche' : 'Address search'}</dt>
        <dd>
          {de
            ? 'Zuerst die Haltestellen der Region, dann Haltestellen, Adressen und Orte in ganz Deutschland über Transitous. Photon (komoot, OpenStreetMap) springt ein, wenn Transitous nicht antwortet.'
            : "The region's stops first, then stops, addresses and places across Germany from Transitous. Photon by komoot (OpenStreetMap) takes over when Transitous doesn't answer."}
        </dd>

        <dt>{de ? 'Störungsmeldungen lesen (KI)' : 'Reading disruption reports (AI)'}</dt>
        <dd>
          {de
            ? 'Sprachmodelle über OpenRouter, in dieser Reihenfolge (kostenlose zuerst, das kostenpflichtige mit Tageslimit zuletzt):'
            : 'Language models on OpenRouter, in this order (free ones first, the paid one with a daily cap last):'}
          {models.length > 0 ? (
            <ol className="about__models">
              {models.map(({ model, paid, link }) => (
                <li key={model}>
                  <code>{model}</code>
                  {paid && <span className="about__tag">{de ? 'kostenpflichtig' : 'paid'}</span>}
                  {link.calls_today && (
                    <span className="about__tag">{de ? `heute ${link.calls_today}` : `${link.calls_today} today`}</span>
                  )}
                  {link.quota_exhausted_until && (
                    <span className="about__tag about__tag--off">
                      {de
                        ? `Tageslimit erreicht bis ${link.quota_exhausted_until}`
                        : `daily limit reached until ${link.quota_exhausted_until}`}
                    </span>
                  )}
                </li>
              ))}
            </ol>
          ) : (
            <> {de ? 'keine eingestellt.' : 'none set.'} </>
          )}
          Status: {state(status?.llm)}
          {status?.llm?.answered_by &&
            (de
              ? `; zuletzt geantwortet hat ${status.llm.answered_by}`
              : `; last answered by ${status.llm.answered_by}`)}
          .{' '}
          {de
            ? 'Antwortet keines, liest ein regelbasierter Leser die Meldung.'
            : 'When none answers, a rule-based reader takes over.'}
        </dd>

        <dt>{de ? 'Entscheidungen (KI)' : 'Decisions (AI)'}</dt>
        <dd>
          {de
            ? 'Jev von TypeSafe AI bewertet, wie ernst eine Meldung ist, ob umgeplant werden sollte und wie riskant jeder Umstieg ist: '
            : 'Jev by TypeSafe AI rates how serious a report is, whether to reroute, and how risky each transfer is: '}
          {status ? (status.jev_enabled ? (de ? 'an' : 'on') : de ? 'aus' : 'off') : '…'}.
        </dd>

        <dt>{de ? 'Auslastung und Wartezeiten' : 'Crowding and on-demand waiting times'}</dt>
        <dd>
          {de ? 'Auslastung: ' : 'Crowding: '}
          {status ? trained('capacity') : '…'}. {de ? 'Wartezeiten: ' : 'Waiting times: '}
          {status ? trained('on_demand_wait') : '…'}.
        </dd>
      </dl>
    </dialog>
  );
}
