import { createContext, useContext } from 'react';

/* English text is the key, so English needs no table and the code reads as English.
   {name} placeholders are filled from `vars`. Missing German falls back to English. */

export type Lang = 'en' | 'de';

const DE: Record<string, string> = {
  // Header and shell
  'Trips across Germany': 'Unterwegs in ganz Deutschland',
  'Skip to trip planner': 'Zur Reiseplanung springen',
  'Trip planner': 'Reiseplanung',
  'Service status': 'Dienststatus',
  Timetables: 'Fahrpläne',
  'Language model': 'Sprachmodell',
  'Server offline': 'Server nicht erreichbar',
  working: 'funktioniert',
  'not working': 'funktioniert nicht',
  'not used yet': 'noch nicht genutzt',
  'key works, not used yet': 'Schlüssel gültig, noch nicht genutzt',
  'Off: notices are read by rules': 'Aus: Meldungen werden regelbasiert gelesen',
  'Not working, notices are read by rules: {error}':
    'Funktioniert nicht, Meldungen werden regelbasiert gelesen: {error}',
  '{model} (not used yet)': '{model} (noch nicht genutzt)',
  '{model} (key works, not used yet)': '{model} (Schlüssel gültig, noch nicht genutzt)',
  'How FastMile works': 'So funktioniert FastMile',
  'Switch to German': 'Auf Deutsch umschalten',
  'Switch to English': 'Auf Englisch umschalten',
  Imprint: 'Impressum',
  Privacy: 'Datenschutz',
  Connections: 'Verbindungen',
  Disruptions: 'Störungen',
  Departures: 'Abfahrten',
  'Click the map to set a start, or search on the left': 'Auf die Karte klicken für den Start, oder links suchen',
  'Now click or search the destination': 'Jetzt das Ziel anklicken oder suchen',
  'Dropped pin': 'Gesetzter Punkt',
  'My location': 'Mein Standort',

  // Search
  From: 'Von',
  To: 'Nach',
  'Start: stop, address or place': 'Start: Haltestelle, Adresse oder Ort',
  'Clear {field}': '{field} leeren',
  '{field} suggestions': 'Vorschläge für {field}',
  Stop: 'Haltestelle',
  'Use my location': 'Meinen Standort verwenden',
  'Searching…': 'Suche läuft …',
  'No places found. Check the spelling or add the town, e.g. “Marktstraße 1 Goslar”.':
    'Nichts gefunden. Schreibweise prüfen oder den Ort dazuschreiben, z. B. „Marktstraße 1 Goslar“.',
  'Search is not answering. Try again, or click the place on the map.':
    'Die Suche antwortet nicht. Erneut versuchen oder den Ort auf der Karte anklicken.',
  'Swap start and destination': 'Start und Ziel tauschen',
  Swap: 'Tauschen',
  'This browser can’t share your location. Search for the start instead.':
    'Dieser Browser kann den Standort nicht teilen. Bitte den Start suchen.',
  'Location access is blocked. Allow it in the browser, or search for the start.':
    'Standortzugriff ist blockiert. Im Browser erlauben oder den Start suchen.',
  'Couldn’t find your location. Search for the start instead.': 'Standort nicht gefunden. Bitte den Start suchen.',

  // Time and modes
  When: 'Wann',
  Now: 'Jetzt',
  'Leave at': 'Abfahrt um',
  'Arrive by': 'Ankunft bis',
  'Set the time you need to be there.': 'Wann musst du da sein?',
  'Modes to use': 'Verkehrsmittel',
  Walk: 'Fußweg',
  Bus: 'Bus',
  Tram: 'Straßenbahn',
  Train: 'Zug',
  'U-Bahn': 'U-Bahn',
  Bike: 'Rad',
  Scooter: 'E-Scooter',
  'Car-sharing': 'Carsharing',
  'On-demand': 'Rufbus',
  Ferry: 'Fähre',

  // Connections
  'Couldn’t plan this trip': 'Diese Fahrt ließ sich nicht planen',
  'Where to?': 'Wohin?',
  'Search for a start and destination above, or click two points on the map. FastMile combines buses, trains, bikes and on-demand rides, and warns you when a disruption breaks your trip.':
    'Oben Start und Ziel suchen oder zwei Punkte auf der Karte anklicken. FastMile kombiniert Bus, Bahn, Rad und Rufbus und warnt, wenn eine Störung deine Fahrt unterbricht.',
  'Finding connections': 'Verbindungen werden gesucht',
  'No connections found': 'Keine Verbindungen gefunden',
  'Try a later time, or allow more modes.': 'Eine spätere Zeit wählen oder mehr Verkehrsmittel erlauben.',
  'Your planned trip': 'Deine geplante Fahrt',
  Direct: 'Direkt',
  '1 change': '1 Umstieg',
  '{n} changes': '{n} Umstiege',
  '{pct}% likely to work': 'klappt zu {pct} %',
  'Chance the whole chain works as planned': 'Wahrscheinlichkeit, dass die ganze Kette wie geplant klappt',
  'Transfers checked by Jev': 'Umstiege von Jev geprüft',
  Cancelled: 'Fällt aus',
  cancelled: 'fällt aus',
  'Live: {n} min late': 'Live: {n} Min. später',
  'on time': 'pünktlich',
  'min late': 'Min. Verspätung',
  '{n} min': '{n} Min.',
  Fastest: 'Am schnellsten',
  'Most reliable': 'Am zuverlässigsten',
  Recommended: 'Empfohlen',
  'Leave latest': 'Spätestens los',
  'likely full': 'wahrscheinlich voll',
  crowded: 'stark besetzt',
  busy: 'gut besetzt',
  'seats free': 'Sitzplätze frei',
  'towards {headsign}': 'Richtung {headsign}',
  'From {stop}': 'Ab {stop}',
  ', platform {platform}': ', Gleis {platform}',
  'Get off at {stop} after {n} stops, {min} min': 'Ausstieg {stop} nach {n} Halten, {min} Min.',
  'Get off at {stop} after 1 stop, {min} min': 'Ausstieg {stop} nach 1 Halt, {min} Min.',
  'Walk {min} min to {stop}': '{min} Min. Fußweg zu {stop}',
  'Walk {min} min ({distance}) to {stop}': '{min} Min. Fußweg ({distance}) zu {stop}',
  'On-demand shuttle to {stop}': 'Rufbus nach {stop}',
  'Book now: pickup in about {wait} min, {min} min in total':
    'Jetzt buchen: Abholung in etwa {wait} Min., insgesamt {min} Min.',
  '{mode} to {stop}': '{mode} nach {stop}',
  'Pick up at {stop}': 'Abholen an {stop}',
  ' ({n} available now)': ' ({n} jetzt frei)',
  'Book by phone': 'Telefonisch anmelden',
  'Departures here': 'Abfahrten hier',
  'Back to all connections': 'Zurück zu allen Verbindungen',
  'Your trip still works': 'Deine Fahrt klappt noch',
  'No active disruption affects it.': 'Keine aktive Störung betrifft sie.',
  'New connections from {place}, {time}': 'Neue Verbindungen ab {place}, {time}',
  'Outside the dashed FastMile region: connections from the Germany-wide timetable (Transitous).':
    'Außerhalb der gestrichelten FastMile-Region: Verbindungen aus dem deutschlandweiten Fahrplan (Transitous).',
  'Your trip is affected: {summary}': 'Deine Fahrt ist betroffen: {summary}',
  'Find new connections': 'Neue Verbindungen suchen',
  'Dismiss notice': 'Hinweis schließen',
  'Replanning failed. Try again.': 'Neuplanung fehlgeschlagen. Bitte erneut versuchen.',
  'The server didn’t answer. Check that the backend is running.': 'Der Server antwortet nicht. Läuft das Backend?',
  'Jev advises: {strategy}': 'Jev rät: {strategy}',
  ', urgency {pct}%': ', Dringlichkeit {pct} %',
  '. {mode} options are listed first.': '. Verbindungen mit {mode} stehen oben.',
  'wait it out': 'abwarten',
  'skip the broken part': 'den gestörten Abschnitt umgehen',
  'replan the whole trip': 'die ganze Fahrt neu planen',
  'switch mode': 'auf ein anderes Verkehrsmittel umsteigen',
  'turn back': 'umkehren',

  // Disruptions
  'Disruption reports': 'Störungsmeldungen',
  'Paste a disruption notice in German or English. FastMile reads which lines, stops and times it affects, and avoids them when planning. Notices from the operator’s live feed appear here automatically.':
    'Eine Störungsmeldung auf Deutsch oder Englisch einfügen. FastMile liest Linien, Haltestellen und Zeiten heraus und umfährt sie bei der Planung. Meldungen aus dem Live-Feed der Betreiber erscheinen hier automatisch.',
  'Bus 830 closed': 'Bus 830 gesperrt',
  'RB 82 delayed': 'RB 82 verspätet',
  'Station closed': 'Bahnhof gesperrt',
  'Disruption notice': 'Störungsmeldung',
  'Source of the notice': 'Quelle der Meldung',
  'Manual report': 'Eigene Meldung',
  'Reading…': 'Wird gelesen …',
  'Add report': 'Meldung hinzufügen',
  'The report couldn’t be added. Try again.': 'Die Meldung konnte nicht hinzugefügt werden. Bitte erneut versuchen.',
  'No active disruptions right now.': 'Gerade keine aktiven Störungen.',
  'Line not running': 'Linie fährt nicht',
  'Stops skipped': 'Halte entfallen',
  Delays: 'Verspätungen',
  'Fewer vehicles': 'Weniger Fahrzeuge',
  Disruption: 'Störung',
  'Lines {lines}': 'Linien {lines}',
  'Mark resolved': 'Als erledigt markieren',
  '{start} until {end}': '{start} bis {end}',
  'further notice': 'auf Weiteres',
  '{lines} lines and {stops} stops matched': '{lines} Linien und {stops} Haltestellen zugeordnet',
  'read by the language model': 'vom Sprachmodell gelesen',
  'Recommended instead: {alternatives}': 'Stattdessen empfohlen: {alternatives}',
  'read by the rule-based reader': 'regelbasiert gelesen',
  'from the operator’s live feed': 'aus dem Live-Feed des Betreibers',
  'source unknown': 'Quelle unbekannt',
  'Severity {severity}, Jev {pct}% sure': 'Schwere {severity}, Jev {pct} % sicher',
  critical: 'kritisch',
  high: 'hoch',
  medium: 'mittel',
  low: 'niedrig',
  Unknown: 'Unbekannt',

  // Departures board
  'Departures from {stop}': 'Abfahrten ab {stop}',
  Time: 'Zeit',
  Line: 'Linie',
  Destination: 'Ziel',
  Platform: 'Gleis',
  'Pick a stop: search a start above, or click a stop on the map.':
    'Haltestelle wählen: oben einen Start suchen oder eine Haltestelle auf der Karte anklicken.',
  'No departures in the next hours.': 'In den nächsten Stunden keine Abfahrten.',
  'Loading departures': 'Abfahrten werden geladen',
  'Couldn’t load departures': 'Abfahrten konnten nicht geladen werden',
  'Updated {time}': 'Stand {time}',
  'FastMile timetable': 'FastMile-Fahrplan',
  'Germany-wide timetable': 'Deutschlandweiter Fahrplan',
  'Info:': 'Information:',
  'Phone to book': 'Anmeldung nötig',
  'Click for departures': 'Klicken für Abfahrten',
  'You are here: {name}': 'Du bist hier: {name}',
  Start: 'Start',

  // About and legal
  Close: 'Schließen',
  'The server didn’t answer, so live status is missing.': 'Der Server antwortet nicht, daher fehlt der Live-Status.',
};

const STRINGS: Record<Lang, Record<string, string>> = { en: {}, de: DE };

export type Translate = (text: string, vars?: Record<string, string | number>) => string;

function fill(text: string, vars?: Record<string, string | number>): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (match, name: string) => (name in vars ? String(vars[name]) : match));
}

export function translate(lang: Lang, text: string, vars?: Record<string, string | number>): string {
  return fill(STRINGS[lang][text] ?? text, vars);
}

export const STORAGE_KEY = 'fastmile.lang';

/** Saved choice, then the browser's language. */
export function initialLang(): Lang {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'en' || saved === 'de') return saved;
  } catch {
    // Storage blocked: fall through to the browser language
  }
  return navigator.language.toLowerCase().startsWith('de') ? 'de' : 'en';
}

export interface LangState {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: Translate;
}

export const LangContext = createContext<LangState>({ lang: 'en', setLang: () => undefined, t: (s, v) => fill(s, v) });

export function useLang(): LangState {
  return useContext(LangContext);
}
