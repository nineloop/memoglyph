import type { ReactNode } from 'react'

export type IconName =
  | 'alert' | 'archive' | 'arrow-left' | 'check' | 'clock' | 'close' | 'cpu' | 'error'
  | 'feather' | 'folder' | 'menu' | 'monitor' | 'moon' | 'more' | 'plus' | 'refresh'
  | 'pause' | 'play' | 'report' | 'search' | 'settings' | 'sparkles' | 'sun'
  | 'trash' | 'upload' | 'users' | 'volume'

const glyphs: Record<IconName, ReactNode> = {
  alert: <><path d="M10.3 3.3 2.6 16.5a1 1 0 0 0 .9 1.5h15a1 1 0 0 0 .9-1.5L11.7 3.3a.8.8 0 0 0-1.4 0Z"/><path d="M11 8v4"/><path d="M11 15h.01"/></>,
  archive: <><path d="M4 7h14v11H4z"/><path d="M3 3h16v4H3z"/><path d="M9 11h4"/></>,
  'arrow-left': <><path d="m12 5-6 6 6 6"/><path d="M6 11h12"/></>,
  check: <path d="m4 11 4 4 10-10"/>,
  clock: <><circle cx="11" cy="11" r="8"/><path d="M11 6v5l3 2"/></>,
  close: <><path d="m5 5 12 12"/><path d="M17 5 5 17"/></>,
  cpu: <><rect x="5" y="5" width="12" height="12" rx="2"/><path d="M8 2v3M14 2v3M8 17v3M14 17v3M2 8h3M2 14h3M17 8h3M17 14h3"/></>,
  error: <><circle cx="11" cy="11" r="8"/><path d="m8.5 8.5 5 5m0-5-5 5"/></>,
  feather: <><path d="M19 3c-6.5.2-11.4 3.2-13.8 8.2L3 16l4.8-2.2C12.8 11.4 15.8 6.5 16 0"/><path d="M6 16 3 19"/></>,
  folder: <path d="M3 6.5h6l2-2h8v13H3z"/>,
  menu: <><path d="M4 6h14"/><path d="M4 11h14"/><path d="M4 16h14"/></>,
  monitor: <><rect x="3" y="4" width="16" height="11" rx="1.5"/><path d="M8 19h6M11 15v4"/></>,
  moon: <path d="M17.5 12.5A6.5 6.5 0 1 1 9.5 4.5a5 5 0 0 0 8 8Z"/>,
  pause: <><rect x="6" y="4" width="3.4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="12.6" y="4" width="3.4" height="14" rx="1" fill="currentColor" stroke="none"/></>,
  play: <path d="M7 4.5v13l11-6.5Z" fill="currentColor" stroke="none"/>,
  more: <><circle cx="5" cy="11" r="1" fill="currentColor" stroke="none"/><circle cx="11" cy="11" r="1" fill="currentColor" stroke="none"/><circle cx="17" cy="11" r="1" fill="currentColor" stroke="none"/></>,
  plus: <><path d="M11 4v14"/><path d="M4 11h14"/></>,
  refresh: <><path d="M18 7V3l-2 2a8 8 0 1 0 2.2 7"/><path d="M18 3h-4"/></>,
  report: <><path d="M6 3h8l3 3v13H6z"/><path d="M14 3v4h4"/><path d="M9 11h5M9 15h5"/></>,
  search: <><circle cx="10" cy="10" r="6"/><path d="m14.5 14.5 4 4"/></>,
  settings: <><path d="M4 6h8"/><circle cx="15" cy="6" r="2"/><path d="M18 6h1M3 16h1"/><circle cx="7" cy="16" r="2"/><path d="M10 16h9M3 11h4"/><circle cx="10" cy="11" r="2"/><path d="M13 11h6"/></>,
  sparkles: <><path d="m11 3 1.2 3.8L16 8l-3.8 1.2L11 13 9.8 9.2 6 8l3.8-1.2Z"/><path d="m17 13 .7 2.3L20 16l-2.3.7L17 19l-.7-2.3L14 16l2.3-.7Z"/></>,
  sun: <><circle cx="11" cy="11" r="4"/><path d="M11 2v2M11 18v2M2 11h2M18 11h2M4.6 4.6l1.4 1.4M16 16l1.4 1.4M17.4 4.6 16 6M6 16l-1.4 1.4"/></>,
  trash: <><path d="M5 7h12l-1 12H6Z"/><path d="M4 7h14M8 7V4h6v3M9 10v6m4-6v6"/></>,
  upload: <><path d="M11 15V3m0 0L7 7m4-4 4 4"/><path d="M4 14v5h14v-5"/></>,
  users: <><circle cx="8" cy="8" r="3"/><path d="M2.5 18c.4-3.2 2.2-5 5.5-5s5.1 1.8 5.5 5"/><path d="M14 6.5a3 3 0 0 1 0 5.5m1 1c2.5.4 3.8 2 4 5"/></>,
  volume: <><path d="M4 9h3l4-4v12l-4-4H4z"/><path d="M14 8a4 4 0 0 1 0 6m2-8a7 7 0 0 1 0 10"/></>,
}

export function Icon({ name, className = '' }: { name: IconName; className?: string }) {
  return (
    <svg className={`icon ${className}`} viewBox="0 0 22 22" fill="none"
         stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true">
      {glyphs[name]}
    </svg>
  )
}
