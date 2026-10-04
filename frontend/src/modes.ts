import {
  Bicycle,
  Bus,
  Car,
  PersonSimpleWalk,
  Scooter,
  Train,
  Tram,
  Van,
  type Icon,
} from '@phosphor-icons/react';
import type { RouteLeg } from './services/api';

/** Icons pair with every mode label, so modes never rely on colour alone. */
export const MODE_ICON: Record<string, Icon> = {
  walk: PersonSimpleWalk,
  bus: Bus,
  tram: Tram,
  train: Train,
  subway: Train,
  bike: Bicycle,
  scooter: Scooter,
  car_share: Car,
  on_demand: Van,
};

/** Fallback colors when the feed gives a line none (gtfs.de mostly doesn't). */
export const MODE_COLORS: Record<string, string> = {
  walk: '#94a3b8',
  bus: '#a23b72',
  tram: '#c8322a',
  train: '#2b4c9a',
  subway: '#1e5aa8',
  ferry: '#1f7a8c',
  bike: '#0e7a4f',
  scooter: '#2a9d8f',
  car_share: '#6c4ab6',
  on_demand: '#d9731a',
};

const MODE_LABELS: Record<string, string> = {
  walk: 'Walk',
  bus: 'Bus',
  tram: 'Tram',
  train: 'Train',
  subway: 'U-Bahn',
  bike: 'Bike',
  scooter: 'Scooter',
  car_share: 'Car-sharing',
  on_demand: 'On-demand',
};

/** Modes the traveller can switch on/off (walking is always allowed). */
export const SELECTABLE_MODES = ['bus', 'train', 'tram', 'bike', 'scooter', 'car_share', 'on_demand'];

export function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? mode.replace('_', ' ');
}

export function legColor(leg: Pick<RouteLeg, 'mode' | 'line_color'>): string {
  return leg.line_color || MODE_COLORS[leg.mode] || '#4a5565';
}

/** True when a background is light enough to need dark text (WCAG relative luminance). */
export function isLight(hex: string): boolean {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return false;
  const [r, g, b] = [0, 2, 4].map((i) => {
    const c = parseInt(m[1].slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45;
}
