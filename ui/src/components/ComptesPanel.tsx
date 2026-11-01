import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'

import { changerActivation, creerCompte, listerComptes, type Compte } from '../api'
import { useLang } from '../lib/langContext'
import { useSession } from './AuthGate'

/**
 * Vue « Comptes », réservée à l'administrateur : pas d'inscription
 * libre, c'est lui qui crée les comptes. Un compte se désactive et se réactive ;
 * rien n'est supprimé en v1. Le serveur refuse ces routes à un non-administrateur
 * et refuse de désactiver le dernier administrateur actif ; le panneau ne fait
 * qu'afficher ses réponses.
 */
export function ComptesPanel() {
  const { t } = useLang()
  const [comptes, setComptes] = useState<Compte[]>([])
  const [erreur, setErreur] = useState('')
  const [avis, setAvis] = useState('')
  const [identifiant, setIdentifiant] = useState('')
  const [motDePasse, setMotDePasse] = useState('')
  const [admin, setAdmin] = useState(false)

  const charger = useCallback(() => {
    listerComptes().then(setComptes).catch((exc: Error) => setErreur(exc.message))
  }, [])
  useEffect(() => { charger() }, [charger])

  const basculer = async (compte: Compte) => {
    setErreur('')
    setAvis('')
    try {
      await changerActivation(compte.identifiant, !compte.actif)
      charger()
    } catch (exc) {
      setErreur(t('auth.error.failed', { detail: (exc as Error).message }))
    }
  }

  const creer = async (event: FormEvent) => {
    event.preventDefault()
    setErreur('')
    setAvis('')
    try {
      const { utilisateur } = await creerCompte(identifiant, motDePasse, admin)
      setAvis(t('accounts.created', { name: utilisateur.identifiant }))
      setIdentifiant('')
      setMotDePasse('')
      setAdmin(false)
      charger()
    } catch (exc) {
      setErreur(t('auth.error.failed', { detail: (exc as Error).message }))
    }
  }

  return (
    <section className="accounts-panel" aria-labelledby="accounts-title">
      <h2 id="accounts-title">{t('accounts.title')}</h2>
      <p>{t('accounts.lead')}</p>
      <table className="accounts-table">
        <thead>
          <tr>
            <th>{t('accounts.col.user')}</th>
            <th>{t('accounts.col.role')}</th>
            <th>{t('accounts.col.state')}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {comptes.map((compte) => (
            <tr key={compte.identifiant}>
              <td>{compte.identifiant}</td>
              <td>{compte.admin ? t('accounts.role.admin') : t('accounts.role.member')}</td>
              <td>{compte.actif ? t('accounts.state.active') : t('accounts.state.inactive')}</td>
              <td>
                <button type="button" className="chip" onClick={() => { void basculer(compte) }}>
                  {compte.actif ? t('accounts.deactivate') : t('accounts.reactivate')}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form className="accounts-form" onSubmit={creer} aria-label={t('accounts.new')}>
        <h3>{t('accounts.new')}</h3>
        <label className="field" htmlFor="accounts-username">{t('auth.field.username')}
          <input id="accounts-username" type="text" required autoComplete="off" value={identifiant}
                 onChange={(event) => setIdentifiant(event.target.value)} />
        </label>
        <label className="field" htmlFor="accounts-password">{t('auth.field.password')}
          <input id="accounts-password" type="password" required autoComplete="new-password"
                 value={motDePasse} onChange={(event) => setMotDePasse(event.target.value)} />
        </label>
        <label className="accounts-admin">
          <input type="checkbox" checked={admin} onChange={(event) => setAdmin(event.target.checked)} />
          {t('accounts.new.admin')}
        </label>
        <button type="submit" className="primary">{t('accounts.create')}</button>
      </form>
      {erreur && <p className="auth-error" role="alert">{erreur}</p>}
      {avis && <p role="status">{avis}</p>}
    </section>
  )
}

/** Bouton « Comptes » de la barre du haut, pour l'administrateur seulement. */
export function BoutonComptes() {
  const { t } = useLang()
  const session = useSession()
  const [ouvert, setOuvert] = useState(false)
  if (!session?.utilisateur?.admin) return null
  return (
    <>
      <button type="button" className="chip auth-logout" onClick={() => setOuvert(true)}>
        {t('accounts.open')}
      </button>
      {/* Rendu dans `document.body` : la barre du haut crée un contexte
          d'empilement qui piégerait un calque en position fixe. */}
      {ouvert && createPortal(
        <div className="accounts-overlay" role="dialog" aria-modal="true" aria-labelledby="accounts-title">
          <div className="accounts-dialog">
            <button type="button" className="chip accounts-close" onClick={() => setOuvert(false)}>
              {t('accounts.close')}
            </button>
            <ComptesPanel />
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}
