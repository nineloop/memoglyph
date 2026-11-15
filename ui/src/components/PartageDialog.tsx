import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'

import { creerPartage, getPartages, listerComptes, retirerPartage, type Partage } from '../api'
import { useSession } from './AuthGate'
import { useLang } from '../lib/langContext'
import { phraseDErreur } from '../lib/erreurs'
import { Icon } from './Icon'

/**
 * Dialogue « Partager » d'un projet ou d'une réunion (partage v1).
 *
 * Lecture seule, avec un compte existant, sans lien public : on saisit un
 * identifiant, on voit avec qui l'objet est déjà partagé, on retire. Les refus
 * du serveur (compte inconnu, réunion ultra-confidentielle…) s'affichent tels
 * qu'il les écrit : c'est lui qui décide ce qui se partage.
 *
 * Rendu dans `document.body`, comme la vue des comptes : la barre du haut et la
 * vue d'une réunion créent des contextes d'empilement.
 */
export function PartageDialog({ type, cible, nom, onClose }: {
  type: Partage['type']
  cible: string
  nom: string
  onClose: () => void
}) {
  const { t, lang } = useLang()
  const [partages, setPartages] = useState<Partage[] | null>(null)
  const [invite, setInvite] = useState('')
  const [erreur, setErreur] = useState('')
  const [enCours, setEnCours] = useState(false)
  // Les comptes proposés dans le champ (le champ libre reste possible). L'administrateur lit la liste des comptes actifs, qu'il a déjà le droit
  // de voir ; un membre n'a aucune route qui liste les comptes : on ne lui propose que ceux qu'il connaît déjà par ses partages.
  const session = useSession()
  const moi = session?.utilisateur?.identifiant ?? ''
  const admin = session?.utilisateur?.admin === true
  const idListe = useId()
  const [comptesAdmin, setComptesAdmin] = useState<string[]>([])
  const [comptesConnus, setComptesConnus] = useState<string[]>([])
  useEffect(() => {
    if (!admin) return
    let vivant = true
    listerComptes()
      .then((comptes) => { if (vivant) setComptesAdmin(comptes.filter((c) => c.actif && c.identifiant !== moi).map((c) => c.identifiant)) })
      .catch(() => { if (vivant) setComptesAdmin([]) })
    return () => { vivant = false }
  }, [admin, moi])
  // Pas de proposition pour un compte avec qui cette cible est déjà partagée.
  const dejaPartages = new Set((partages ?? []).map((p) => p.invite.toLowerCase()))
  const suggestions = (admin ? comptesAdmin : comptesConnus).filter((c) => !dejaPartages.has(c.toLowerCase()))

  const charger = useCallback(() => {
    getPartages()
      .then((tous) => {
        setPartages(tous.donnes.filter((p) => p.type === type && p.cible === cible))
        const connus = [...tous.donnes.map((p) => p.invite), ...tous.recus.map((p) => p.proprietaire)]
        setComptesConnus([...new Set(connus)].filter((c) => c && c.toLowerCase() !== moi.toLowerCase()))
      })
      .catch((e: unknown) => setErreur(phraseDErreur(t, lang, e, t('repli.partagesCharger'))))
  }, [type, cible, t, lang, moi])
  useEffect(() => { charger() }, [charger])

  // Échap ferme le dialogue où que soit le focus, comme celui des « Appareils » : un
  // écouteur sur le fond ne le reçoit que si le focus est à l'intérieur.
  useEffect(() => {
    const echap = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', echap)
    return () => document.removeEventListener('keydown', echap)
  }, [onClose])

  async function partager(event: FormEvent) {
    event.preventDefault()
    if (!invite.trim() || enCours) return
    setEnCours(true)
    setErreur('')
    try {
      await creerPartage(type, cible, invite.trim())
      setInvite('')
      charger()
    } catch (e) {
      setErreur(phraseDErreur(t, lang, e, t('repli.partageCreer')))
    } finally {
      setEnCours(false)
    }
  }

  async function retirer(partage: Partage) {
    setErreur('')
    try {
      await retirerPartage(partage.id)
      charger()
    } catch (e) {
      setErreur(phraseDErreur(t, lang, e, t('repli.partageRetirer')))
    }
  }

  return createPortal(
    <div className="modal-backdrop"
         onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <form className="merge-dialog partage-dialog" role="dialog" aria-modal="true"
            aria-labelledby="partage-titre" onSubmit={partager}>
        <div className="merge-dialog-head">
          <div>
            <span className="action-scope">{t('share.lead')}</span>
            <h2 id="partage-titre">{t(type === 'projet' ? 'share.title.projet' : 'share.title.reunion', { nom })}</h2>
          </div>
          <button type="button" className="icon-btn" aria-label={t('share.close')} onClick={onClose}>×</button>
        </div>

        <label className="field" htmlFor="partage-invite">
          <span>{t('share.field')}</span>
          {/* Nom explicite, égal au texte visible : certains arbres d'accessibilité ne le
              tirent pas du label englobant. */}
          <input id="partage-invite" type="text" autoFocus autoComplete="off" value={invite}
                 aria-label={t('share.field')} list={suggestions.length > 0 ? idListe : undefined}   /* lié à la liste seulement s'il y a des comptes à proposer */
                 disabled={enCours} onChange={(event) => setInvite(event.target.value)} />
        </label>

        <datalist id={idListe}>
          {suggestions.map((compte) => <option key={compte} value={compte} />)}
        </datalist>

        {erreur && <p className="error" role="alert"><Icon name="error" /> {erreur}</p>}

        <div className="dialog-actions">
          <button type="submit" className="btn accent" disabled={!invite.trim() || enCours}>
            {enCours ? t('share.sending') : t('share.submit')}
          </button>
        </div>

        <h3>{t('share.current')}</h3>
        {partages && partages.length === 0 && <p className="muted small">{t('share.none')}</p>}
        {partages && partages.length > 0 && (
          <ul className="partage-liste">
            {partages.map((partage) => (
              <li key={partage.id}>
                <span>{partage.invite}</span>
                <button type="button" className="btn sm danger-subtle"
                        aria-label={t('share.revoke.aria', { invite: partage.invite })}
                        onClick={() => { void retirer(partage) }}>
                  {t('share.revoke')}
                </button>
              </li>
            ))}
          </ul>
        )}
      </form>
    </div>,
    document.body,
  )
}
