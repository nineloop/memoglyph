import { useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import type { Partage, Project } from '../api'
import { PORTEE_PARTAGES, PORTEE_TOUS } from '../api'
import { useLang } from '../lib/langContext'
import { filtrerProjets, initiales } from '../lib/navigationEtat'
import { libelleProjet } from '../lib/libelleProjet'
import { ecrireUrl } from '../lib/urlState'
import { Icon } from './Icon'

/**
 * Le panneau des projets de la Bibliothèque.
 *
 * C'est le SEUL endroit, avec le fil d'Ariane, où l'on choisit ce qu'on consulte : le menu natif
 * « Portée consultée » a disparu. Chaque projet est un vrai LIEN (adresse copiable, clic du
 * milieu), dont le clic simple est intercepté pour naviguer sans recharger. Replié, le panneau
 * devient une colonne d'icônes qui garde les mêmes destinations.
 *
 * Le même contenu sert au tiroir mobile : un seul composant de liste, deux enveloppes.
 */

type PropsListe = {
  projets: Project[]
  recus: Partage[]
  /** Identifiant consulté : un projet, `all` ou `shared` (les partages reçus). */
  consulte: string
  replie?: boolean
  surConsulter: (cible: string) => void
  surNouveau: () => void
}

const adresse = (projet: string): string => ecrireUrl({ vue: 'library', projet })

function Lien({ cible, nom, detail, consulte, replie, surConsulter, icone, avecInitiales }: {
  cible: string
  nom: string
  detail?: string
  consulte: string
  replie: boolean
  surConsulter: (cible: string) => void
  icone?: 'folder' | 'users'
  /** Replié, un vrai projet montre ses initiales : des icônes identiques ne disent pas lequel est lequel. */
  avecInitiales?: boolean
}) {
  const actif = consulte === cible
  const clic = (e: MouseEvent<HTMLAnchorElement>) => {
    // Un clic modifié (nouvel onglet, copie) garde le comportement du navigateur.
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    surConsulter(cible)
  }
  return (
    <a href={adresse(cible)} className={`projet-lien ${actif ? 'on' : ''}`} title={nom}
       aria-current={actif ? 'page' : undefined} onClick={clic}>
      {replie && avecInitiales
        ? <span className="projet-initiales" aria-hidden="true">{initiales(nom)}</span>
        : <span className="projet-glyphe" aria-hidden="true"><Icon name={icone ?? 'folder'} /></span>}
      <span className={replie ? 'projet-nom sr-only' : 'projet-nom'}>{nom}</span>
      {!replie && detail !== undefined && <span className="projet-detail">{detail}</span>}
    </a>
  )
}

export function ProjetsListe({ projets, recus, consulte, replie = false, surConsulter, surNouveau }: PropsListe) {
  const { t } = useLang()
  const [filtre, setFiltre] = useState('')
  // Les noms sont ceux qu'on AFFICHE : la réception du système suit la langue, et le filtre aussi.
  const avecId = useMemo(
    () => projets.filter((p) => p.id).map((p) => ({ ...p, name: libelleProjet(p.name, t, p.kind) })),
    [projets, t])
  const total = avecId.reduce((somme, p) => somme + p.count, 0)
  const boite = avecId.find((p) => p.kind === 'inbox')
  const reguliers = filtrerProjets(avecId.filter((p) => p.kind !== 'inbox'), filtre)
  const partages = filtrerProjets(
    recus.filter((p) => p.type === 'projet').map((p) => ({ ...p, name: p.nom ?? p.cible })), filtre)
  const montrerTous = !filtre.trim() || filtrerProjets([{ name: t('scope.allMine') }], filtre).length > 0
  const montrerBoite = boite && (!filtre.trim() || filtrerProjets([boite], filtre).length > 0)

  return (
    <>
      {!replie && (
        <input className="search projets-filtre" type="search" value={filtre}
               aria-label={t('nav.panel.filter')} placeholder={t('nav.panel.filter')}
               onChange={(e) => setFiltre(e.target.value)} />
      )}
      <div className="projets-defilement">
        <ul className="projets-liste">
          {montrerTous && (
            <li><Lien cible={PORTEE_TOUS} nom={t('scope.allMine')} detail={String(total)} consulte={consulte}
                      replie={replie} surConsulter={surConsulter} /></li>
          )}
          {montrerBoite && boite && (
            <li><Lien cible={boite.id!} nom={boite.name} avecInitiales detail={String(boite.count)} consulte={consulte}
                      replie={replie} surConsulter={surConsulter} /></li>
          )}
        </ul>
        {reguliers.length > 0 && (
          <>
            {!replie && <h3 className="projets-groupe">{t('nav.group.projects')}</h3>}
            <ul className="projets-liste">
              {reguliers.map((p) => (
                <li key={p.id!}><Lien cible={p.id!} avecInitiales nom={p.owner === null ? t('app.scope.ownerless', { nom: p.name }) : p.name} detail={String(p.count)} consulte={consulte}
                                      replie={replie} surConsulter={surConsulter} /></li>
              ))}
            </ul>
          </>
        )}
        {recus.length > 0 && (
          <>
            {!replie && <h3 className="projets-groupe">{t('nav.group.shared')}</h3>}
            <ul className="projets-liste">
              {!filtre.trim() && (
                <li><Lien cible={PORTEE_PARTAGES} nom={t('nav.group.sharedAll')} consulte={consulte} icone="users"
                          replie={replie} surConsulter={surConsulter} /></li>
              )}
              {partages.map((p) => (
                <li key={p.id}><Lien cible={p.cible} avecInitiales nom={p.name} detail={p.proprietaire} consulte={consulte}
                                     icone="users" replie={replie} surConsulter={surConsulter} /></li>
              ))}
            </ul>
          </>
        )}
        {filtre.trim() && !montrerTous && !montrerBoite && reguliers.length === 0 && partages.length === 0 && (
          <p className="muted small projets-vide">{t('nav.panel.empty')}</p>
        )}
      </div>
      <button type="button" className={`btn projets-nouveau ${replie ? 'icone' : ''}`} onClick={surNouveau}
              aria-label={t('nav.panel.new')} title={t('nav.panel.new')}>
        <Icon name="plus" />{!replie && <span>{t('nav.panel.new')}</span>}
      </button>
    </>
  )
}

type PropsPanneau = PropsListe & {
  replie: boolean
  surBasculer: (replie: boolean) => void
}

export function ProjetsPanel({ replie, surBasculer, ...liste }: PropsPanneau) {
  const { t } = useLang()
  return (
    <aside className={`projets-panneau ${replie ? 'replie' : ''}`}>
      <nav aria-label={t('nav.panel.aria')} className="projets-nav">
        <div className="projets-tete">
          {!replie && <h2>{t('nav.panel.title')}</h2>}
          <button type="button" className="icon-btn" onClick={() => surBasculer(!replie)}
                  aria-expanded={!replie}
                  aria-label={replie ? t('nav.panel.expand') : t('nav.panel.collapse')}
                  title={replie ? t('nav.panel.expand') : t('nav.panel.collapse')}>
            <Icon name={replie ? 'menu' : 'arrow-left'} />
          </button>
        </div>
        <ProjetsListe replie={replie} {...liste} />
      </nav>
    </aside>
  )
}

type PropsTiroir = PropsListe & {
  ouvert: boolean
  surFermer: () => void
}

const FOCALISABLES = 'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Le tiroir des projets sur petit écran : dialogue modal, focus piégé, Échap, retour du focus. */
export function ProjetsTiroir({ ouvert, surFermer, surConsulter, ...liste }: PropsTiroir) {
  const { t } = useLang()
  const racine = useRef<HTMLDivElement | null>(null)
  const avant = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!ouvert) return
    avant.current = document.activeElement as HTMLElement | null
    racine.current?.querySelector<HTMLElement>(FOCALISABLES)?.focus()
    return () => {
      const precedent = avant.current
      // Le tiroir se démonte avec `ouvert` : le focus revient là où il était.
      queueMicrotask(() => precedent?.focus?.())
    }
  }, [ouvert])

  if (!ouvert) return null

  const surClavier = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { e.stopPropagation(); surFermer(); return }
    if (e.key !== 'Tab') return
    const elements = [...(racine.current?.querySelectorAll<HTMLElement>(FOCALISABLES) ?? [])]
    if (elements.length === 0) return
    const premier = elements[0]
    const dernier = elements[elements.length - 1]
    if (e.shiftKey && document.activeElement === premier) { e.preventDefault(); dernier.focus() }
    else if (!e.shiftKey && document.activeElement === dernier) { e.preventDefault(); premier.focus() }
  }

  return (
    <div className="tiroir-fond" onMouseDown={(e) => { if (e.target === e.currentTarget) surFermer() }}
         onClick={(e) => { if (e.target === e.currentTarget) surFermer() }}>
      <div ref={racine} className="tiroir" role="dialog" aria-modal="true" aria-label={t('nav.panel.aria')}
           onKeyDown={surClavier}>
        <div className="projets-tete">
          <h2>{t('nav.panel.title')}</h2>
          <button type="button" className="icon-btn" onClick={surFermer} aria-label={t('nav.drawer.close')}>
            <Icon name="close" />
          </button>
        </div>
        <ProjetsListe {...liste} surConsulter={(cible) => { surConsulter(cible); surFermer() }} />
      </div>
    </div>
  )
}
