import { useState } from 'react';
import type { Disruption } from '../../services/api';
import { formatTime } from '../../services/api';
import { useLang } from '../../i18n';

interface ReportsProps {
  disruptions: Disruption[];
  onSubmit: (text: string, source: string) => Promise<void>;
  onResolve: (id: number) => void;
}

// Example reports to try the pipeline with (lines exist in the region's network)
const EXAMPLES = [
  {
    label: 'Bus 830 closed',
    source: 'KVG Betriebsmeldung',
    text: 'Wegen eines Unfalls ist die Buslinie 830 zwischen Goslar und Clausthal-Zellerfeld bis voraussichtlich 18:00 Uhr gesperrt.',
  },
  {
    label: 'RB 82 delayed',
    source: 'DB Störungsticker',
    text: 'RB 82 Goslar – Bad Harzburg: Verspätungen von bis zu 15 Minuten wegen einer Signalstörung.',
  },
  {
    label: 'Station closed',
    source: 'DB Störungsticker',
    text: 'Polizeieinsatz: Bad Harzburg Bahnhof ist ab sofort gesperrt. Züge fahren ohne Halt durch.',
  },
];

const EFFECTS: Record<string, string> = {
  suspended: 'Line not running',
  stops_not_served: 'Stops skipped',
  station_closed: 'Station closed',
  delayed: 'Delays',
  reduced_availability: 'Fewer vehicles',
  other: 'Disruption',
};

const READ_BY: Record<string, string> = {
  llm: 'read by the language model',
  rules: 'read by the rule-based reader',
  'gtfs-rt': 'from the operator’s live feed',
};

export default function Reports({ disruptions, onSubmit, onResolve }: ReportsProps) {
  const { t } = useLang();
  const [text, setText] = useState('');
  const [source, setSource] = useState(() => t('Manual report'));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onSubmit(text, source);
      setText('');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('The report couldn’t be added. Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const active = disruptions.filter((d) => d.status === 'active');

  return (
    <section className="reports" aria-label={t('Disruption reports')}>
      <p className="reports__intro">
        {t(
          'Paste a disruption notice in German or English. FastMile reads which lines, stops and times it affects, and avoids them when planning. Notices from the operator’s live feed appear here automatically.',
        )}
      </p>
      <div className="reports__examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.label}
            type="button"
            className="btn btn--quiet btn--small"
            onClick={() => {
              setText(ex.text);
              setSource(ex.source);
            }}
          >
            {t(ex.label)}
          </button>
        ))}
      </div>
      <textarea
        id="report-text"
        aria-label={t('Disruption notice')}
        rows={4}
        placeholder="Buslinie 830 zwischen Goslar und Clausthal bis 10:00 Uhr gesperrt"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="reports__row">
        <input
          id="report-source"
          aria-label={t('Source of the notice')}
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
        <button type="button" className="btn" disabled={busy || text.trim().length < 5} onClick={submit}>
          {busy ? t('Reading…') : t('Add report')}
        </button>
      </div>
      {error && <p className="reports__error">{error}</p>}

      {active.length === 0 ? (
        <p className="reports__intro">{t('No active disruptions right now.')}</p>
      ) : (
        <ul className="report-list">
          {active.map((d) => (
            <li key={d.id} className={`report report--${d.severity || 'medium'}`}>
              <span className="report__severity" aria-hidden="true" />
              <div>
                <div className="report__head">
                  <span className="report__severity-label">{t(d.severity || 'Unknown')}</span>
                  <span className="report__effect">{t(EFFECTS[d.effect || 'other'] || 'Disruption')}</span>
                  {d.affected_lines.length > 0 && (
                    <span>{t('Lines {lines}', { lines: d.affected_lines.join(', ') })}</span>
                  )}
                  <button type="button" className="btn btn--quiet btn--small" onClick={() => onResolve(d.id)}>
                    {t('Mark resolved')}
                  </button>
                </div>
                <p className="report__summary">{d.summary || d.raw_text}</p>
                {(d.alternatives?.length ?? 0) > 0 && (
                  <p className="report__alternatives">
                    {t('Recommended instead: {alternatives}', { alternatives: d.alternatives!.join('; ') })}
                  </p>
                )}
                <div className="report__facts">
                  <span className="tnum">
                    {t('{start} until {end}', {
                      start: d.start_time ? formatTime(d.start_time) : t('Now'),
                      end: d.end_time ? formatTime(d.end_time) : t('further notice'),
                    })}
                  </span>
                  <span>
                    {t('{lines} lines and {stops} stops matched', {
                      lines: d.affected_routes.length,
                      stops: d.affected_stops.length,
                    })}
                  </span>
                  <span>{t(READ_BY[d.extraction_method ?? ''] ?? 'source unknown')}</span>
                  {d.confidence != null && (
                    <span>
                      {t('Severity {severity}, Jev {pct}% sure', {
                        severity: t(d.severity ?? 'Unknown'),
                        pct: Math.round(d.confidence * 100),
                      })}
                    </span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
