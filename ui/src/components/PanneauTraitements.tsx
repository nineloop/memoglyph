import { useEffect, useId, useRef, useState } from 'react'
import type { ListeTraitements, Traitement } from '../api'
import { traduireEtape } from '../lib/i18n'
import type { MessageKey } from '../lib/messages'
import { useLang } from '../lib/langContext'
import { libelleProjet } from '../lib/libelleProjet'
import { etatAffiche, separer, valeurDepuis } from '../lib/traitements'
import type { EtatAffiche } from '../lib/traitements'
import { ecrireUrl } from '../lib/urlState'
import { Icon } from './Icon'

/** Les natures qui ont un libellé ; « traitement », la nature générique, n'apprend rien sur une carte. */
const NATURES_CONNUES = new Set(['transcription', 'compte-rendu', 'faits', 'voix', 'archives'])
import type { IconName } from './Icon'

/** L'état se lit par un LIBELLÉ et par une FORME, jamais par la seule couleur. */
const ETATS: Record<EtatAffiche, { cle: MessageKey; icone: IconName }> = {
  en_attente: { cle: 'traitements.state.waiting', icone: 'clock' },
  en_cours: { cle: 'traitements.state.running', icone: 'play' },
  termine: { cle: 'traitements.state.done', icone: 'check' },
  echec: { cle: 'traitements.state.error', icone: 'alert' },
}

type Reunion = NonNullable<Traitement['meeting']>

function Ligne({ tr, maintenant, surOuvrirReunion }: {
  tr: Traitement
  maintenant: number
  surOuvrirReunion: (reunion: Reunion) => void
}) {
  const { t, lang } = useLang()
  const etat = etatAffiche(tr)
  const { cle, icone } = ETATS[etat]
  const titre = tr.titre || t(`traitements.nature.${tr.nature}` as MessageKey)
  const depuis = valeurDepuis(tr.debut, maintenant)
  // En cours ou en attente : « depuis 4 min » ; terminé ou échoué : « il y a 2 min » (comme l'anglais).
  const fini = etat === 'termine' || etat === 'echec'
  const famille = fini ? 'ago' : 'since'
  const depuisTexte = depuis.unite === 'now' ? t('traitements.since.now')
    : t(`traitements.${famille}.${depuis.unite === 'min' ? 'min' : 'h'}` as MessageKey, { n: depuis.n })
  const pct = Math.round(Math.max(0, Math.min(1, tr.progression)) * 100)
  // La nature d'abord (« Compte rendu · étape 1 sur 3 ») : deux cartes au même titre se distinguent. La nature
  // générique n'apprend rien.
  const nature = NATURES_CONNUES.has(tr.nature)
    ? t(`traitements.nature.${tr.nature}` as MessageKey) : ''
  const details = [
    nature,
    tr.projet_nom ? libelleProjet(tr.projet_nom, t) : '',
    etat === 'en_cours' && tr.etape && tr.etapes ? t('traitements.step', { n: tr.etape, total: tr.etapes }) : '',
    depuisTexte,
  ].filter(Boolean)
  const reunion = etat === 'termine' ? tr.meeting : null
  // Un échec en UNE phrase ; le détail brut (texte d'exception) va à l'infobulle.
  const messageEchec = etat === 'echec'
    ? (tr.erreur_code
      ? traduireEtape(lang, { code: tr.erreur_code, params: tr.erreur_params, message: t('traitements.failed') })
      : t('traitements.failed'))
    : ''
  return (
    <li className={`traitement traitement-${etat}`}>
      <div className="traitement-tete">
        <span className={`traitement-etat etat-${etat}`}><Icon name={icone} /> {t(cle)}</span>
        <strong className="traitement-titre">{titre}</strong>
      </div>
      <p className="traitement-details muted small">{details.join(' · ')}</p>
      {etat === 'en_attente' && <p className="traitement-attente muted small">{t('traitements.waiting.hint')}</p>}
      {etat === 'en_cours' && (
        <div className="traitement-barre" role="progressbar" aria-valuemin={0} aria-valuemax={100}
             aria-valuenow={pct} aria-label={t('traitements.progress', { pct })}>
          <span style={{ width: `${pct}%` }} />
        </div>
      )}
      {etat === 'echec' && (
        <div className="traitement-echec-bloc">
          <p>{messageEchec}</p>
          {/* Le texte brut de l'exception n'est pas traduit : à part, sous un titre qui, lui, l'est. */}
          {tr.erreur && (
            <details className="traitement-echec-detail">
              <summary>{t('traitements.failed.tech')}</summary>
              <code>{tr.erreur}</code>
            </details>
          )}
        </div>
      )}
      {reunion && (
        <a className="traitement-ouvrir"
           href={ecrireUrl({ vue: 'library', projet: reunion.project_id ?? tr.projet_id ?? undefined, reunion: reunion.meeting_id })}
           aria-label={t('traitements.open.aria', { titre })}
           onClick={(e) => {
             if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
             e.preventDefault()
             surOuvrirReunion(reunion)
           }}>
          {t('traitements.open')}
        </a>
      )}
    </li>
  )
}

/**
 * Le panneau des traitements : ce qui tourne, ce qui attend, ce qui vient de finir, depuis n'importe quel
 * écran. Les actifs d'abord, puis les cinq derniers terminés ; « Tout voir » déplie l'historique reçu.
 */
export function PanneauTraitements({ liste, maintenant, surFermer, surOuvrirReunion }: {
  liste: ListeTraitements | null
  /** Secondes depuis l'époque ; injectable pour les tests. */
  maintenant?: number
  surFermer: () => void
  surOuvrirReunion: (reunion: Reunion) => void
}) {
  const { t } = useLang()
  const [tout, setTout] = useState(false)
  const titreId = useId()
  const racine = useRef<HTMLElement>(null)
  const now = maintenant ?? Date.now() / 1000
  const { actifs, termines } = separer(liste, tout)
  const totalTermines = separer(liste, true).termines.length
  const aPlus = totalTermines > separer(liste, false).termines.length

  useEffect(() => {
    const echap = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      // Sous une fenêtre modale (Partager, Appareils…), Échap lui appartient.
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return
      surFermer()
      document.querySelector<HTMLElement>('.indicateur-traitements')?.focus()
    }
    document.addEventListener('keydown', echap)
    return () => document.removeEventListener('keydown', echap)
  }, [surFermer])

  const vide = actifs.length === 0 && termines.length === 0
  return (
    <aside className="panneau-traitements" ref={racine} role="dialog" aria-modal="false"
           aria-labelledby={titreId}>
      <header className="panneau-traitements-tete">
        <h2 id={titreId}>{t('traitements.panel.title')}</h2>
        <button type="button" className="icon-btn" onClick={surFermer} aria-label={t('traitements.panel.close')}>
          <Icon name="close" />
        </button>
      </header>
      {vide && <p className="muted">{t('traitements.panel.empty')}</p>}
      {actifs.length > 0 && (
        <>
          <h3 className="panneau-traitements-groupe">{t('traitements.panel.active')}</h3>
          <ul className="traitements-liste">
            {actifs.map((tr) => <Ligne key={tr.id} tr={tr} maintenant={now} surOuvrirReunion={surOuvrirReunion} />)}
          </ul>
        </>
      )}
      {termines.length > 0 && (
        <>
          <h3 className="panneau-traitements-groupe">{t('traitements.panel.recent')}</h3>
          <ul className="traitements-liste">
            {termines.map((tr) => <Ligne key={tr.id} tr={tr} maintenant={now} surOuvrirReunion={surOuvrirReunion} />)}
          </ul>
        </>
      )}
      <p className="muted small panneau-traitements-note">{t('traitements.panel.note')}</p>
      {(aPlus || tout) && totalTermines > 0 && (
        <button type="button" className="btn" onClick={() => setTout((v) => !v)}>
          {tout ? t('traitements.seeLess') : t('traitements.seeAll', { n: totalTermines })}
        </button>
      )}
    </aside>
  )
}
