import { useId, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import type { Project } from '../api'
import { useLang } from '../lib/langContext'
import { destinationsPossibles, filtrerProjets } from '../lib/navigationEtat'
import { libelleProjet } from '../lib/libelleProjet'
import { Icon } from './Icon'

/**
 * « Ranger dans » : la DESTINATION de la prochaine transcription.
 *
 * Un combobox (champ + liste) : on tape pour filtrer, on choisit un projet ou on en crée un.
 * Ce choix ne change JAMAIS ce que la Bibliothèque consulte : le composant ne reçoit que
 * la destination et ne connaît pas le projet consulté. Seuls les projets du compte y figurent :
 * un projet reçu en lecture seule (ou sans identifiant) ne peut pas recevoir une réunion.
 */
type Props = {
  projets: Project[]
  destination: string
  surChoisir: (id: string) => void
  /** Crée le projet puis le rend ; le composant le choisit ensuite. */
  surCreer: (nom: string) => Promise<Project>
  /** Vrai quand la destination vient de la mémoire (« dernier projet utilisé »). */
  proposee?: boolean
  desactive?: boolean
  /** Nom affiché tant que le compte n'a pas encore de projet de destination (« Non classé » à créer au premier envoi). */
  nomParDefaut?: string
}

const sansAccents = (texte: string): string => texte.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

type Entree = { cle: string; libelle: string; id?: string; creer?: string }

export function RangerDans({ projets, destination, surChoisir, surCreer, proposee = false, desactive = false, nomParDefaut = '' }: Props) {
  const { t } = useLang()
  const base = useId()
  const champ = useRef<HTMLInputElement>(null)
  const possibles = useMemo(
    () => destinationsPossibles(projets).map((p) => ({ ...p, name: libelleProjet(p.name, t, p.kind) })),
    [projets, t])
  const courant = possibles.find((p) => p.id === destination)
  const [saisie, setSaisie] = useState<string | null>(null)  // null : on ne tape pas
  const [ouvert, setOuvert] = useState(false)
  const [actif, setActif] = useState(0)
  const [occupe, setOccupe] = useState(false)
  const [erreur, setErreur] = useState(false)

  const filtre = saisie ?? ''
  const entrees: Entree[] = useMemo(() => {
    const liste: Entree[] = filtrerProjets(possibles, filtre).map((p) => ({ cle: p.id, libelle: p.name, id: p.id }))
    const nom = filtre.trim()
    if (nom && !possibles.some((p) => sansAccents(p.name) === sansAccents(nom) || sansAccents(p.project) === sansAccents(nom))) {
      liste.push({ cle: '__creer', libelle: t('destination.create', { nom }), creer: nom })
    }
    return liste
  }, [possibles, filtre, t])

  const idListe = `${base}-liste`
  const idAide = `${base}-aide`
  const idOption = (i: number) => `${base}-opt-${i}`

  function fermer() { setOuvert(false); setSaisie(null) }

  async function valider(entree: Entree) {
    if (entree.creer) {
      setOccupe(true); setErreur(false)
      try {
        const cree = await surCreer(entree.creer)
        if (cree.id) surChoisir(cree.id)
        fermer()
      } catch {
        setErreur(true)
      } finally {
        setOccupe(false)
      }
      return
    }
    if (entree.id) surChoisir(entree.id)
    fermer()
  }

  function ouvrir() {
    if (desactive) return
    setOuvert(true)
    const i = entrees.findIndex((e) => e.id === destination)
    setActif(i >= 0 ? i : 0)
  }

  function touche(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!ouvert) return ouvrir()
      setActif((a) => Math.min(a + 1, entrees.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActif((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter' && ouvert && entrees[actif]) {
      e.preventDefault()
      void valider(entrees[actif])
    } else if (e.key === 'Escape' && ouvert) {
      e.preventDefault()
      fermer()
    }
  }

  return (
    <div className="ranger-dans">
      <label className="field-label" htmlFor={`${base}-champ`}><strong>{t('destination.label')}</strong></label>
      <div className="ranger-champ">
        <span className="destination-icon" aria-hidden="true"><Icon name="folder" /></span>
        <input
          id={`${base}-champ`} ref={champ} type="text" role="combobox" autoComplete="off"
          aria-expanded={ouvert} aria-controls={idListe} aria-autocomplete="list"
          aria-activedescendant={ouvert && entrees[actif] ? idOption(actif) : undefined}
          aria-describedby={idAide} placeholder={t('destination.filter')}
          disabled={desactive || occupe}
          value={saisie ?? courant?.name ?? (destination ? '' : nomParDefaut)}
          onFocus={(e) => { e.currentTarget.select(); ouvrir() }}
          onClick={ouvrir}
          onChange={(e) => { setSaisie(e.target.value); setOuvert(true); setActif(0) }}
          onBlur={fermer}
          onKeyDown={touche}
        />
      </div>
      {ouvert && (
        <ul id={idListe} role="listbox" aria-label={t('destination.label')} className="ranger-liste">
          {entrees.map((e, i) => (
            <li key={e.cle} id={idOption(i)} role="option" aria-selected={e.id === destination}
                className={`${i === actif ? 'actif' : ''} ${e.creer ? 'creer' : ''}`}
                onMouseDown={(ev) => ev.preventDefault()}  /* garde le focus : le blur fermerait la liste avant le clic */
                onClick={() => void valider(e)}>
              {e.libelle}
            </li>
          ))}
        </ul>
      )}
      {ouvert && !entrees.some((e) => e.id) && <p className="ranger-vide" role="status">{t('destination.empty')}</p>}
      <p id={idAide} className="ranger-aide">
        {t('destination.help')}{proposee && <> <span className="ranger-propose">{t('destination.suggested')}</span></>}
      </p>
      {occupe && <p role="status" className="ranger-aide">{t('destination.creating')}</p>}
      {erreur && <p role="alert" className="error">{t('destination.error')}</p>}
    </div>
  )
}
