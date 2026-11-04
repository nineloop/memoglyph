import { useEffect, useId, useRef, useState } from 'react'
import { AnnulationError, annulerTraitement } from '../api'
import type { ListeTraitements, Traitement } from '../api'
import { traduireEtape } from '../lib/i18n'
import type { MessageKey } from '../lib/messages'
import { useLang } from '../lib/langContext'
import { libelleProjet } from '../lib/libelleProjet'
import { annulable, etatAffiche, separer, valeurDepuis } from '../lib/traitements'
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
  // Neutre : un choix de la personne, pas une panne. Une forme (croix) et un libellé, jamais la seule couleur.
  annule: { cle: 'traitements.state.cancelled', icone: 'close' },
}

type Reunion = NonNullable<Traitement['meeting']>

function Ligne({ tr, maintenant, surOuvrirReunion, surAnnuler, annulationEnCours }: {
  tr: Traitement
  maintenant: number
  surOuvrirReunion: (reunion: Reunion) => void
  surAnnuler: (tr: Traitement) => void
  annulationEnCours: boolean
}) {
  const { t, lang } = useLang()
  const etat = etatAffiche(tr)
  const { cle, icone } = ETATS[etat]
  const titre = tr.titre || t(`traitements.nature.${tr.nature}` as MessageKey)
  const depuis = valeurDepuis(tr.debut, maintenant)
  // En cours ou en attente : « depuis 4 min » ; terminé ou échoué : « il y a 2 min » (comme l'anglais).
  const fini = etat === 'termine' || etat === 'echec' || etat === 'annule'
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
    <li className={`traitement traitement-${etat}`} data-traitement={tr.id} tabIndex={-1}>
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
      {annulable(tr) && (
        <button type="button" className="btn traitement-annuler" disabled={annulationEnCours}
                aria-label={t('traitements.cancel.aria', { titre })} onClick={() => surAnnuler(tr)}>
          {t('traitements.cancel')}
        </button>
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
export function PanneauTraitements({ liste, maintenant, surFermer, surOuvrirReunion, rafraichir, annuler = annulerTraitement }: {
  liste: ListeTraitements | null
  /** Secondes depuis l'époque ; injectable pour les tests. */
  maintenant?: number
  surFermer: () => void
  surOuvrirReunion: (reunion: Reunion) => void
  /** Relève la liste tout de suite (après une annulation, la ligne change de groupe sans attendre la relève suivante). */
  rafraichir?: () => void
  /** Injectable pour les tests ; `POST /api/jobs/{id}/cancel` par défaut. */
  annuler?: (jobId: string) => Promise<void>
}) {
  const { t } = useLang()
  const [tout, setTout] = useState(false)
  const [annulation, setAnnulation] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const titreId = useId()
  const racine = useRef<HTMLElement>(null)
  const titreRef = useRef<HTMLHeadingElement>(null)
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

  /** Annuler un traitement en attente : UNE confirmation, puis la liste est relevée et le résultat se dit. */
  async function surAnnuler(tr: Traitement) {
    if (annulation) return
    if (!window.confirm(t('traitements.cancel.confirm'))) return
    // Le focus se décide avant la requête : la ligne cliquée va quitter les actifs. La ligne suivante, sinon le
    // déclencheur du panneau (comme Échap).
    const index = actifs.findIndex((x) => x.id === tr.id)
    const suivante = actifs.slice(index + 1).concat(actifs.slice(0, Math.max(index, 0))).find((x) => x.id !== tr.id)
    setAnnulation(tr.id)
    setMessage('')
    let dit = t('traitements.cancelled.message')
    let reussi = true
    // Annulé, ou perdu la course (le traitement a obtenu sa place) ou oublié du serveur : la liste affichée est
    // périmée dans ces trois cas. Une panne inconnue ne dit rien de l'état du traitement.
    let relever = true
    try {
      await annuler(tr.id)
    } catch (caught) {
      reussi = false
      if (caught instanceof AnnulationError) {
        dit = t(caught.raison === 'impossible' ? 'traitements.cancel.impossible' : 'traitements.cancel.gone')
      } else {
        dit = t('traitements.cancel.failed')
        relever = false
      }
    } finally {
      setAnnulation(null)
    }
    setMessage(dit)
    if (relever) rafraichir?.()
    // L'annulation n'a pas eu lieu : le bouton cliqué était désactivé pendant la requête et va disparaître de la liste
    // relevée. Le focus va au TITRE du panneau (toujours affiché, contrairement à la zone d'état vide), jamais au document.
    if (!reussi) titreRef.current?.focus()
    if (reussi) {
      const cible = suivante
        ? document.querySelector<HTMLElement>(`[data-traitement="${CSS.escape(suivante.id)}"]`)
        : document.querySelector<HTMLElement>('.indicateur-traitements')
      cible?.focus()
    }
  }

  const vide = actifs.length === 0 && termines.length === 0
  return (
    <aside className="panneau-traitements" ref={racine} role="dialog" aria-modal="false"
           aria-labelledby={titreId}>
      <header className="panneau-traitements-tete">
        <h2 id={titreId} ref={titreRef} tabIndex={-1}>{t('traitements.panel.title')}</h2>
        <button type="button" className="icon-btn" onClick={surFermer} aria-label={t('traitements.panel.close')}>
          <Icon name="close" />
        </button>
      </header>
      {/* Toujours présente : une zone d'état qui existe avant le message est annoncée par les lecteurs d'écran. */}
      <p role="status" className="muted small panneau-traitements-etat">{message}</p>
      {vide && <p className="muted">{t('traitements.panel.empty')}</p>}
      {actifs.length > 0 && (
        <>
          <h3 className="panneau-traitements-groupe">{t('traitements.panel.active')}</h3>
          <ul className="traitements-liste">
            {actifs.map((tr) => <Ligne key={tr.id} tr={tr} maintenant={now} surOuvrirReunion={surOuvrirReunion}
                                  surAnnuler={surAnnuler} annulationEnCours={annulation !== null} />)}
          </ul>
        </>
      )}
      {termines.length > 0 && (
        <>
          <h3 className="panneau-traitements-groupe">{t('traitements.panel.recent')}</h3>
          <ul className="traitements-liste">
            {termines.map((tr) => <Ligne key={tr.id} tr={tr} maintenant={now} surOuvrirReunion={surOuvrirReunion}
                                  surAnnuler={surAnnuler} annulationEnCours={annulation !== null} />)}
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
