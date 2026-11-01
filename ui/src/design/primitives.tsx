import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import { Icon } from '../components/Icon'
import type { IconName } from '../components/Icon'
import { useTheme } from './theme'
import type { ThemeChoice } from './theme'
import { useLang } from '../lib/langContext'
import './primitives.css'

/** Concatène des classes en ignorant les valeurs vides. */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}

/* ------------------------------------------------------------------ Button */
type ButtonVariant = 'primary' | 'ghost' | 'subtle' | 'danger'
type ButtonSize = 'sm' | 'md'
export function Button({
  variant = 'ghost', size = 'md', icon, iconRight, children, className, ...rest
}: {
  variant?: ButtonVariant
  size?: ButtonSize
  icon?: IconName
  iconRight?: IconName
  children?: ReactNode
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cx('kw-btn', `kw-btn--${variant}`, `kw-btn--${size}`, !children && 'kw-btn--icon', className)}
      {...rest}
    >
      {icon && <Icon name={icon} />}
      {children && <span>{children}</span>}
      {iconRight && <Icon name={iconRight} />}
    </button>
  )
}

/* -------------------------------------------------------------------- Card */
export function Card({ as: As = 'div', className, children, ...rest }:
  { as?: 'div' | 'article' | 'section'; className?: string; children?: ReactNode }
  & HTMLAttributes<HTMLElement>) {
  return <As className={cx('kw-card', className)} {...rest}>{children}</As>
}

/* -------------------------------------------------------------------- Chip */
export function Chip({ active, icon, children, className, ...rest }:
  { active?: boolean; icon?: IconName; children: ReactNode }
  & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" className={cx('kw-chip', active && 'is-active', className)}
            aria-pressed={active} {...rest}>
      {icon && <Icon name={icon} />}{children}
    </button>
  )
}

/* ------------------------------------------------------------------- Badge */
export type BadgeTone = 'neutral' | 'accent' | 'ok' | 'warn' | 'err' | 'llm'
export function Badge({ tone = 'neutral', children, className }:
  { tone?: BadgeTone; children: ReactNode; className?: string }) {
  return <span className={cx('kw-badge', `kw-badge--${tone}`, className)}>{children}</span>
}

/* ------------------------------------------------------- SegmentedControl */
export function SegmentedControl<T extends string>({ options, value, onChange, ariaLabel, size = 'md' }: {
  options: { value: T; label: ReactNode; icon?: IconName; title?: string }[]
  value: T
  onChange: (v: T) => void
  ariaLabel: string
  size?: ButtonSize
}) {
  return (
    <div className={cx('kw-seg', `kw-seg--${size}`)} role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button key={o.value} type="button" title={o.title}
                className={cx('kw-seg__item', value === o.value && 'is-active')}
                aria-pressed={value === o.value}
                onClick={() => onChange(o.value)}>
          {o.icon && <Icon name={o.icon} />}
          {o.label && <span>{o.label}</span>}
        </button>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------- Field */
export function Field({ label, hint, children, htmlFor, className }:
  { label: ReactNode; hint?: ReactNode; children: ReactNode; htmlFor?: string; className?: string }) {
  return (
    <label className={cx('kw-field', className)} htmlFor={htmlFor}>
      <span className="kw-field__label">{label}</span>
      {children}
      {hint && <span className="kw-field__hint">{hint}</span>}
    </label>
  )
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx('kw-input', className)} {...rest} />
}

/* --------------------------------------------------------------- Skeleton */
export function Skeleton({ w, h = 14, radius = 8, className }:
  { w?: number | string; h?: number | string; radius?: number; className?: string }) {
  return (
    <span className={cx('kw-skel', className)} aria-hidden="true"
          style={{ width: w ?? '100%', height: h, borderRadius: radius }} />
  )
}

/* ------------------------------------------------------------- EmptyState */
export function EmptyState({ icon, title, children, action }:
  { icon?: IconName; title: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="kw-empty">
      {icon && <span className="kw-empty__icon"><Icon name={icon} /></span>}
      <h3 className="kw-empty__title">{title}</h3>
      {children && <p className="kw-empty__body">{children}</p>}
      {action && <div className="kw-empty__action">{action}</div>}
    </div>
  )
}

/* --------------------------------------------------------------- WaveBars */
/**
 * Motif de marque : l'onde. Rendu DÉTERMINISTE (semé) pour rester stable en
 * test et lisible comme une intention, jamais un bruit aléatoire.
 */
function semeur(seed: number): () => number {
  let s = seed % 2147483647
  if (s <= 0) s += 2147483646
  return () => { s = (s * 16807) % 2147483647; return (s - 1) / 2147483646 }
}
export function WaveBars({ bars = 48, seed = 1, played = 0, className, ariaLabel }:
  { bars?: number; seed?: number; played?: number; className?: string; ariaLabel?: string }) {
  const rnd = semeur(seed * 97 + 13)
  const coupe = Math.round(bars * Math.min(Math.max(played, 0), 1))
  const items = Array.from({ length: bars }, (_, k) => {
    const t = k / bars
    const env = 0.35 + 0.65 * Math.sin(t * Math.PI)
    const h = Math.max(8, Math.round((0.2 + 0.8 * rnd()) * env * 100))
    return { h, joue: k < coupe }
  })
  return (
    <span className={cx('kw-wave', className)} role={ariaLabel ? 'img' : undefined} aria-label={ariaLabel} aria-hidden={ariaLabel ? undefined : true}>
      {items.map((b, i) => (
        <i key={i} className={b.joue ? 'is-played' : undefined} style={{ height: `${b.h}%` }} />
      ))}
    </span>
  )
}

/* ------------------------------------------------------------- SpeakerDot */
const TEINTES = ['s1', 's2', 's3', 's4', 's5', 's6'] as const
/** Couleur STABLE par locuteur : dérivée de l'index, cyclique sur 6 teintes. */
export function speakerTint(index: number): string {
  return TEINTES[((index % TEINTES.length) + TEINTES.length) % TEINTES.length]
}
export function SpeakerDot({ index = 0, label, size = 22, className }:
  { index?: number; label?: string; size?: number; className?: string }) {
  const initiale = (label ?? '').trim().charAt(0).toUpperCase()
  return (
    <span className={cx('kw-spk', `kw-spk--${speakerTint(index)}`, className)}
          style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}
          aria-hidden="true">
      {initiale}
    </span>
  )
}

/* ------------------------------------------------------------ ThemeToggle */
export function ThemeToggle() {
  const { choice, setChoice } = useTheme()
  const { t } = useLang()
  const options: { value: ThemeChoice; icon: IconName; title: string }[] = [
    { value: 'light', icon: 'sun', title: t('theme.light') },
    { value: 'dark', icon: 'moon', title: t('theme.dark') },
    { value: 'system', icon: 'monitor', title: t('theme.system') },
  ]
  return (
    <SegmentedControl
      size="sm"
      ariaLabel={t('theme.label')}
      value={choice}
      onChange={setChoice}
      options={options.map((o) => ({ value: o.value, label: '', icon: o.icon, title: o.title }))}
    />
  )
}
