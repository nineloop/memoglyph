import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'

import { creerPartage, getPartages, retirerPartage, type Partage } from '../api'
import { useLang } from '../lib/langContext'
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
  const { t } = useLang()
  const [partages, setPartages] = useState<Partage[] | null>(null)
  const [invite, setInvite] = useState('')
  const [erreur, setErreur] = useState('')
  const [enCours, setEnCours] = useState(false)

  const charger = useCallback(() => {
    getPartages()
      .then((tous) => setPartages(tous.donnes.filter((p) => p.type === type && p.cible === cible)))
      .catch((e: Error) => setErreur(e.message))
  }, [type, cible])
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
      setErreur((e as Error).message)
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
      setErreur((e as Error).message)
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
                 aria-label={t('share.field')}
                 disabled={enCours} onChange={(event) => setInvite(event.target.value)} />
        </label>

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
