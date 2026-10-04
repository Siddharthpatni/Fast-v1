import { useEffect, useRef, useState } from 'react';
import { X } from '@phosphor-icons/react';
import { api, type LegalResponse } from '../../services/api';
import { useLang } from '../../i18n';

export type LegalPage = 'imprint' | 'privacy';

interface LegalProps {
  page: LegalPage | null;
  onClose: () => void;
}

/** Impressum (§ 5 DDG) and privacy notice. Operator details come from OPERATOR_* in .env. */
export default function Legal({ page, onClose }: LegalProps) {
  const { lang, t } = useLang();
  const dialog = useRef<HTMLDialogElement>(null);
  const [info, setInfo] = useState<LegalResponse | null>(null);

  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (page && !el.open) {
      el.showModal();
      api
        .legal()
        .then(setInfo)
        .catch(() => setInfo(null));
    } else if (!page && el.open) {
      el.close();
    }
  }, [page]);

  const de = lang === 'de';
  const op = info?.operator;
  const operator = op ? (
    <address className="legal__operator">
      {op.name}
      <br />
      {op.address.split('\n').map((line) => (
        <span key={line}>
          {line}
          <br />
        </span>
      ))}
      {op.email && <a href={`mailto:${op.email}`}>{op.email}</a>}
      {op.phone && (
        <>
          <br />
          {op.phone}
        </>
      )}
    </address>
  ) : (
    <p className="legal__missing">
      {de
        ? 'Die Angaben zum Betreiber fehlen noch. Sie werden in der Server-Konfiguration (OPERATOR_NAME, OPERATOR_ADDRESS, OPERATOR_EMAIL) eingetragen.'
        : "The operator's details are not set yet. They go in the server configuration (OPERATOR_NAME, OPERATOR_ADDRESS, OPERATOR_EMAIL)."}
    </p>
  );
  const title = page === 'privacy' ? t('Privacy') : t('Imprint');

  return (
    <dialog ref={dialog} className="about legal" aria-labelledby="legal-title" onClose={onClose}>
      <div className="about__head">
        <h2 id="legal-title">{title}</h2>
        <button type="button" className="icon-btn" onClick={onClose} aria-label={t('Close')}>
          <X size={18} aria-hidden="true" />
        </button>
      </div>

      {page === 'imprint' && (
        <>
          <h3>{de ? 'Angaben gemäß § 5 DDG' : 'Information under § 5 DDG (German Digital Services Act)'}</h3>
          {operator}
          <h3>{de ? 'Über das Projekt' : 'About the project'}</h3>
          <p>
            {de
              ? 'FastMile ist ein studentisches Forschungsprojekt der TU Clausthal. Die Fahrtvorschläge sind ohne Gewähr; maßgeblich sind die Angaben der Verkehrsunternehmen.'
              : "FastMile is a student research project at TU Clausthal. Trip suggestions come without guarantee; the transport operators' own information is binding."}
          </p>
          <h3>{de ? 'Daten und Karten' : 'Data and maps'}</h3>
          <ul className="legal__list">
            <li>{de ? 'Karte' : 'Map'}: © basemap.de / BKG (CC BY 4.0)</li>
            <li>{de ? 'Fahrpläne' : 'Timetables'}: DELFI e.V., GTFS.de (CC BY 4.0)</li>
            <li>
              {de ? 'Deutschlandweite Verbindungen und Suche' : 'Germany-wide connections and search'}: Transitous
            </li>
            <li>{de ? 'Wege, Adressen' : 'Streets, addresses'}: © OpenStreetMap contributors (ODbL)</li>
          </ul>
        </>
      )}

      {page === 'privacy' && (
        <>
          <h3>{de ? 'Verantwortlich' : 'Controller'}</h3>
          {operator}
          <h3>{de ? 'Was verarbeitet wird' : 'What is processed'}</h3>
          <dl className="about__list">
            <dt>{de ? 'Server-Protokoll' : 'Server log'}</dt>
            <dd>
              {de
                ? 'Bei jedem Aufruf speichert der Server IP-Adresse, Zeitpunkt und aufgerufene Adresse (darin auch Suchbegriffe), um den Dienst zu betreiben und Missbrauch zu erkennen (Art. 6 Abs. 1 lit. f DSGVO). Anfragen pro IP-Adresse werden zwei Minuten lang gezählt, um Überlastung zu verhindern.'
                : 'For each request the server logs the IP address, time and requested address (which includes search words), to run the service and spot abuse (Art. 6(1)(f) GDPR). Requests per IP address are counted for two minutes to prevent overload.'}
            </dd>
            <dt>{de ? 'Fahrtsuche' : 'Trip search'}</dt>
            <dd>
              {de
                ? 'Gesuchte und gewählte Orte werden zur Berechnung an Transitous (Gemeinschaftsprojekt, transitous.org), an den Routing-Dienst der FOSSGIS e.V. (OSRM) und ersatzweise an Photon (komoot GmbH) übermittelt. FastMile speichert geplante Fahrten nicht; die Fahrt steht nur in der Adresszeile deines Browsers.'
                : 'Places you search and pick are sent to Transitous (community project, transitous.org), the FOSSGIS e.V. routing service (OSRM) and, as a fallback, Photon (komoot GmbH) to compute the trip. FastMile does not store planned trips; the trip lives only in your browser’s address bar.'}
            </dd>
            <dt>{de ? 'Standort' : 'Location'}</dt>
            <dd>
              {de
                ? 'Nur wenn du „Meinen Standort verwenden“ drückst und es im Browser erlaubst; er wird wie ein gesuchter Ort behandelt.'
                : 'Only when you press "Use my location" and allow it in the browser; it is then treated like a searched place.'}
            </dd>
            <dt>{de ? 'Störungsmeldungen' : 'Disruption reports'}</dt>
            <dd>
              {de
                ? 'Eingegebene Meldungen werden in der Datenbank gespeichert und zum Auslesen an OpenRouter (USA, Weitergabe an den gewählten Modellanbieter) und an TypeSafe AI (Jev) übermittelt. Bitte keine personenbezogenen Daten eintragen.'
                : 'Reports you enter are stored in the database and sent to OpenRouter (USA, passed on to the chosen model provider) and TypeSafe AI (Jev) to read them. Please do not enter personal data.'}
            </dd>
            <dt>{de ? 'Karte' : 'Map'}</dt>
            <dd>
              {de
                ? 'Die Kartenkacheln lädt dein Browser direkt vom Bundesamt für Kartographie und Geodäsie (basemap.de); dabei sieht der Server des BKG deine IP-Adresse. Schriften werden von dieser Seite selbst geladen.'
                : 'Your browser loads map tiles directly from the Federal Agency for Cartography and Geodesy (basemap.de), whose server sees your IP address. Fonts are served by this site itself.'}
            </dd>
            <dt>{de ? 'Im Browser gespeichert' : 'Stored in your browser'}</dt>
            <dd>
              {de
                ? 'Nur die gewählte Sprache (localStorage). Keine Cookies, kein Tracking.'
                : 'Only your language choice (localStorage). No cookies, no tracking.'}
            </dd>
          </dl>
          <h3>{de ? 'Deine Rechte' : 'Your rights'}</h3>
          <p>
            {de
              ? 'Du hast das Recht auf Auskunft, Berichtigung, Löschung, Einschränkung der Verarbeitung, Datenübertragbarkeit und Widerspruch (Art. 15–21 DSGVO) sowie auf Beschwerde bei einer Datenschutz-Aufsichtsbehörde (Art. 77 DSGVO). Wende dich dazu an den oben genannten Verantwortlichen.'
              : 'You have the right of access, rectification, erasure, restriction of processing, data portability and objection (Art. 15–21 GDPR), and to complain to a data protection supervisory authority (Art. 77 GDPR). Contact the controller named above.'}
          </p>
        </>
      )}
    </dialog>
  );
}
