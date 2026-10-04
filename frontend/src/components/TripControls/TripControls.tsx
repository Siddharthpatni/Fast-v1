import { MODE_COLORS, MODE_ICON, SELECTABLE_MODES, modeLabel } from '../../modes';
import { useLang } from '../../i18n';

export interface TripTime {
  mode: 'depart' | 'arrive';
  value: string; // datetime-local; empty = now (depart only)
}

const CHOICES = [
  ['now', 'Now'],
  ['leave', 'Leave at'],
  ['arrive', 'Arrive by'],
] as const;

/** Current local time as a datetime-local value, e.g. "2026-10-02T17:20". */
function localNow(): string {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

interface TripControlsProps {
  time: TripTime;
  onTimeChange: (time: TripTime) => void;
  modes: string[];
  onModesChange: (modes: string[]) => void;
}

export default function TripControls({ time, onTimeChange, modes, onModesChange }: TripControlsProps) {
  const { t } = useLang();
  const toggle = (mode: string) => {
    onModesChange(modes.includes(mode) ? modes.filter((m) => m !== mode) : [...modes, mode]);
  };

  const choice = time.mode === 'arrive' ? 'arrive' : time.value ? 'leave' : 'now';
  const choose = (next: typeof choice) => {
    if (next === 'now') onTimeChange({ mode: 'depart', value: '' });
    else if (next === 'leave') onTimeChange({ mode: 'depart', value: time.value || localNow() });
    else onTimeChange({ mode: 'arrive', value: time.value });
  };

  return (
    <div className="controls">
      <div className="controls__time">
        <div className="segmented" role="radiogroup" aria-label={t('When')}>
          {CHOICES.map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="radio"
              aria-checked={choice === key}
              className={`segmented__option${choice === key ? ' segmented__option--on' : ''}`}
              onClick={() => choose(key)}
            >
              {t(label)}
            </button>
          ))}
        </div>
        {choice !== 'now' && (
          <input
            type="datetime-local"
            aria-label={choice === 'leave' ? t('Leave at') : t('Arrive by')}
            value={time.value}
            onChange={(e) => onTimeChange({ ...time, value: e.target.value })}
          />
        )}
      </div>
      {time.mode === 'arrive' && !time.value && (
        <p className="controls__hint">{t('Set the time you need to be there.')}</p>
      )}
      <div className="modes" role="group" aria-label={t('Modes to use')}>
        {SELECTABLE_MODES.map((mode) => {
          const on = modes.includes(mode);
          const ModeIcon = MODE_ICON[mode];
          return (
            <button
              key={mode}
              type="button"
              className={`mode-toggle${on ? ' mode-toggle--on' : ''}`}
              aria-pressed={on}
              onClick={() => toggle(mode)}
              style={{ '--mode-color': MODE_COLORS[mode] } as React.CSSProperties}
            >
              {ModeIcon && <ModeIcon size={16} weight={on ? 'bold' : 'regular'} aria-hidden="true" />}
              {t(modeLabel(mode))}
            </button>
          );
        })}
      </div>
    </div>
  );
}
