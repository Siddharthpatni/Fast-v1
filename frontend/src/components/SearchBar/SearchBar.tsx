import { useEffect, useId, useRef, useState } from 'react';
import { ArrowsDownUp, Bus, Crosshair, MapPin, X } from '@phosphor-icons/react';
import { api, type Place } from '../../services/api';
import { useLang } from '../../i18n';

const DEBOUNCE_MS = 300;
const MIN_CHARS = 3;

interface PlaceInputProps {
  label: string;
  placeholder: string;
  value: Place | null;
  onSelect: (place: Place | null) => void;
  action?: React.ReactNode;
}

/** One search field with type-ahead suggestions (stops first, then addresses). */
function PlaceInput({ label, placeholder, value, onSelect, action }: PlaceInputProps) {
  const { t } = useLang();
  const id = useId();
  const [text, setText] = useState(value?.name ?? '');
  const [results, setResults] = useState<Place[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [status, setStatus] = useState<'idle' | 'searching' | 'empty' | 'error'>('idle');
  const timer = useRef<number | undefined>(undefined);
  const latestQuery = useRef('');
  const pickFirst = useRef(false); // Enter was pressed before the suggestions arrived

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const choose = (place: Place) => {
    pickFirst.current = false;
    setText(place.name);
    setOpen(false);
    setResults([]);
    setStatus('idle');
    onSelect(place);
  };

  const search = (query: string, delay = DEBOUNCE_MS) => {
    setText(query);
    setActive(-1);
    window.clearTimeout(timer.current);
    latestQuery.current = query;
    if (query.trim().length < MIN_CHARS) {
      setResults([]);
      setOpen(false);
      setStatus('idle');
      return;
    }
    setStatus('searching');
    timer.current = window.setTimeout(async () => {
      try {
        const found = await api.geocode(query.trim());
        if (latestQuery.current !== query) return; // The user kept typing
        if (pickFirst.current && found.length > 0) {
          choose(found[0]);
          return;
        }
        pickFirst.current = false;
        setResults(found);
        setActive(found.length > 0 ? 0 : -1); // Enter takes the top suggestion
        setOpen(found.length > 0);
        setStatus(found.length > 0 ? 'idle' : 'empty');
      } catch {
        pickFirst.current = false;
        setResults([]);
        setStatus('error');
      }
    }, delay);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && results.length === 0 && text.trim().length >= MIN_CHARS) {
      // Typed and pressed Enter straight away: take the best match as soon as it arrives
      e.preventDefault();
      pickFirst.current = true;
      if (status !== 'searching') search(text, 0);
      return;
    }
    if (!open || results.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((i) => (i + 1) % results.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((i) => (i <= 0 ? results.length - 1 : i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      choose(results[Math.max(active, 0)]);
    } else if (e.key === 'Escape') {
      setOpen(false);
    }
  };

  return (
    <div className="place-input">
      <div className="place-input__field">
        <input
          id={`${id}-input`}
          aria-label={t(label)}
          role="combobox"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${id}-opt-${active}` : undefined}
          autoComplete="off"
          placeholder={t(placeholder)}
          value={text}
          onChange={(e) => search(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => results.length > 0 && setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        />
        {text && (
          <button
            type="button"
            className="place-input__action"
            aria-label={t('Clear {field}', { field: t(label) })}
            onClick={() => {
              setText('');
              setResults([]);
              onSelect(null);
            }}
          >
            <X size={16} aria-hidden="true" />
          </button>
        )}
        {action}
      </div>
      {status !== 'idle' && !open && (
        <p className={`place-input__status${status === 'searching' ? '' : ' place-input__status--warn'}`} role="status">
          {status === 'searching'
            ? t('Searching…')
            : status === 'empty'
              ? t('No places found. Check the spelling or add the town, e.g. “Marktstraße 1 Goslar”.')
              : t('Search is not answering. Try again, or click the place on the map.')}
        </p>
      )}
      {open && results.length > 0 && (
        <ul
          id={`${id}-list`}
          role="listbox"
          aria-label={t('{field} suggestions', { field: t(label) })}
          className="place-input__list"
        >
          {results.map((place, i) => (
            <li
              key={`${place.lat},${place.lon},${i}`}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={i === active}
              className={`place-input__option${i === active ? ' place-input__option--active' : ''}`}
              onMouseDown={(e) => {
                e.preventDefault(); // Keep focus so blur doesn't close the list first
                choose(place);
              }}
            >
              {place.kind === 'stop' ? (
                <Bus className="place-input__kind-icon" size={18} aria-hidden="true" />
              ) : (
                <MapPin className="place-input__kind-icon" size={18} aria-hidden="true" />
              )}
              <span>
                <span className="place-input__name">{place.name}</span>
                <span className="place-input__meta">
                  {place.kind === 'stop'
                    ? place.label?.startsWith('Stop, ')
                      ? place.label.replace('Stop', t('Stop'))
                      : t('Stop')
                    : place.label}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface SearchBarProps {
  from: Place | null;
  to: Place | null;
  onChange: (from: Place | null, to: Place | null) => void;
}

export default function SearchBar({ from, to, onChange }: SearchBarProps) {
  const { t } = useLang();
  // A pick can land after a search delay; it must keep the other end as it is by then
  const ends = useRef({ from, to });
  useEffect(() => {
    ends.current = { from, to };
  }, [from, to]);
  const [locating, setLocating] = useState(false);
  const [geoError, setGeoError] = useState<string | null>(null);

  const useMyLocation = () => {
    if (!navigator.geolocation) {
      setGeoError(t('This browser can’t share your location. Search for the start instead.'));
      return;
    }
    setLocating(true);
    setGeoError(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        onChange({ name: t('My location'), lat: pos.coords.latitude, lon: pos.coords.longitude }, to);
      },
      (err) => {
        setLocating(false);
        setGeoError(
          err.code === err.PERMISSION_DENIED
            ? t('Location access is blocked. Allow it in the browser, or search for the start.')
            : t('Couldn’t find your location. Search for the start instead.'),
        );
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    );
  };

  // Keyed by the place, so a pick on the map (which changes the place) refreshes the text
  const key = (p: Place | null) => (p ? `${p.lat},${p.lon},${p.name}` : 'empty');

  return (
    <div className="search">
      <div className="search__rail" aria-hidden="true">
        <span className="search__pin search__pin--from" />
        <span className="search__pin search__pin--to" />
      </div>
      <PlaceInput
        key={`from-${key(from)}`}
        label="From"
        placeholder="Start: stop, address or place"
        value={from}
        onSelect={(place) => onChange(place, ends.current.to)}
        action={
          <button
            type="button"
            className="place-input__action"
            onClick={useMyLocation}
            disabled={locating}
            aria-label={t('Use my location')}
            title={t('Use my location')}
          >
            <Crosshair size={18} weight={locating ? 'bold' : 'regular'} aria-hidden="true" />
          </button>
        }
      />
      <button
        type="button"
        className="icon-btn search__swap"
        onClick={() => onChange(to, from)}
        disabled={!from && !to}
        aria-label={t('Swap start and destination')}
        title={t('Swap')}
      >
        <ArrowsDownUp size={18} aria-hidden="true" />
      </button>
      <PlaceInput
        key={`to-${key(to)}`}
        label="To"
        placeholder="Destination"
        value={to}
        onSelect={(place) => onChange(ends.current.from, place)}
      />
      {geoError && <p className="search__error">{geoError}</p>}
    </div>
  );
}
