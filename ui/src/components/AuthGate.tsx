import { useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from 'react'

import { connecter, deconnecter, getEtatAuth, installer, type Utilisateur } from '../api'
import { useLang } from '../lib/langContext'
import { SessionContext } from '../lib/sessionContext'
import type { Session } from '../lib/sessionContext'

/**
 * Barrière de connexion : la connexion est obligatoire partout, en local
 * comme sur un serveur. Le backend refuse toute route /api sans session ; cette
 * barrière choisit seulement l'écran à montrer.
 *
 * - aucun compte : le seul écran « Créer le compte administrateur » ;
 * - pas de session : l'écran de connexion ;
 * - session valide : l'application.
 *
 * Backend injoignable : l'application s'affiche quand même, et c'est elle qui
 * dit « Backend injoignable ». Bloquer ici sur un écran vide ne dirait rien de
 * plus, et l'accès reste refusé côté serveur.
 */

type Etape = 'chargement' | 'installation' | 'connexion' | 'pret'

export const EVENEMENT_SESSION_EXPIREE = 'memoglyph:session-expiree'

export function useSession(): Session | null {
  return useContext(SessionContext)
}

/** Une réponse 401 d'une route /api hors authentification signale une session perdue. */
function surveillerSessions(): () => void {
  const fetchOrigine = window.fetch
  window.fetch = async (...args: Parameters<typeof fetch>) => {
    const reponse = await fetchOrigine(...args)
    const url = typeof args[0] === 'string' ? args[0] : args[0] instanceof URL ? args[0].href : args[0].url
    if (reponse.status === 401 && url.includes('/api/') && !url.includes('/api/auth/')) {
      window.dispatchEvent(new Event(EVENEMENT_SESSION_EXPIREE))
    }
    return reponse
  }
  return () => { window.fetch = fetchOrigine }
}

export function AuthGate({ children }: { children: ReactNode }) {
  const { t } = useLang()
  const [etape, setEtape] = useState<Etape>('chargement')
  const [utilisateur, setUtilisateur] = useState<Utilisateur | null>(null)
  const [expiree, setExpiree] = useState(false)

  useEffect(() => {
    let actif = true
    getEtatAuth()
      .then((etat) => {
        if (!actif) return
        setUtilisateur(etat.utilisateur)
        setEtape(etat.installation_requise ? 'installation' : etat.connecte ? 'pret' : 'connexion')
      })
      .catch(() => { if (actif) setEtape('pret') })
    return () => { actif = false }
  }, [])

  useEffect(() => {
    const restaurer = surveillerSessions()
    const surExpiration = () => {
      setUtilisateur(null)
      setExpiree(true)
      setEtape('connexion')
    }
    window.addEventListener(EVENEMENT_SESSION_EXPIREE, surExpiration)
    return () => {
      window.removeEventListener(EVENEMENT_SESSION_EXPIREE, surExpiration)
      restaurer()
    }
  }, [])

  const entrer = useCallback((u: Utilisateur) => {
    setUtilisateur(u)
    setExpiree(false)
    setEtape('pret')
  }, [])

  const deconnexion = useCallback(async () => {
    await deconnecter()
    setUtilisateur(null)
    setEtape('connexion')
  }, [])

  if (etape === 'chargement') {
    return <main className="auth-screen" aria-busy="true"><p>{t('auth.loading')}</p></main>
  }
  if (etape === 'installation') return <EcranInstallation onEntree={entrer} />
  if (etape === 'connexion') return <EcranConnexion onEntree={entrer} expiree={expiree} />
  return (
    <SessionContext.Provider value={{ utilisateur, deconnexion }}>
      {children}
    </SessionContext.Provider>
  )
}

function Champ({ id, label, type = 'text', value, onChange, autoComplete }: {
  id: string; label: string; type?: string; value: string
  onChange: (v: string) => void; autoComplete: string
}) {
  return (
    <label className="field" htmlFor={id}>{label}
      <input id={id} type={type} value={value} required autoComplete={autoComplete}
             onChange={(event) => onChange(event.target.value)} />
    </label>
  )
}

function EcranInstallation({ onEntree }: { onEntree: (u: Utilisateur) => void }) {
  const { t } = useLang()
  const [code, setCode] = useState('')
  const [identifiant, setIdentifiant] = useState('')
  const [motDePasse, setMotDePasse] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [erreur, setErreur] = useState('')
  const [envoi, setEnvoi] = useState(false)

  const soumettre = async (event: FormEvent) => {
    event.preventDefault()
    if (motDePasse !== confirmation) {
      setErreur(t('auth.error.mismatch'))
      return
    }
    setEnvoi(true)
    setErreur('')
    try {
      onEntree((await installer(code, identifiant, motDePasse)).utilisateur)
    } catch (exc) {
      setErreur(t('auth.error.failed', { detail: (exc as Error).message }))
    } finally {
      setEnvoi(false)
    }
  }

  return (
    <main className="auth-screen">
      <form className="auth-card" onSubmit={soumettre} aria-labelledby="auth-install-title">
        <h1 id="auth-install-title">{t('auth.install.title')}</h1>
        <p>{t('auth.install.lead')}</p>
        <Champ id="auth-code" label={t('auth.install.code')} value={code} onChange={setCode}
               autoComplete="one-time-code" />
        <Champ id="auth-username" label={t('auth.field.username')} value={identifiant}
               onChange={setIdentifiant} autoComplete="username" />
        <Champ id="auth-password" label={t('auth.field.password')} type="password" value={motDePasse}
               onChange={setMotDePasse} autoComplete="new-password" />
        <Champ id="auth-confirm" label={t('auth.field.confirm')} type="password" value={confirmation}
               onChange={setConfirmation} autoComplete="new-password" />
        {erreur && <p className="auth-error" role="alert">{erreur}</p>}
        <button type="submit" className="primary" disabled={envoi}>{t('auth.install.submit')}</button>
      </form>
    </main>
  )
}

function EcranConnexion({ onEntree, expiree }: { onEntree: (u: Utilisateur) => void; expiree: boolean }) {
  const { t } = useLang()
  const [identifiant, setIdentifiant] = useState('')
  const [motDePasse, setMotDePasse] = useState('')
  const [erreur, setErreur] = useState('')
  const [envoi, setEnvoi] = useState(false)

  const soumettre = async (event: FormEvent) => {
    event.preventDefault()
    setEnvoi(true)
    setErreur('')
    try {
      onEntree((await connecter(identifiant, motDePasse)).utilisateur)
    } catch (exc) {
      setErreur(t('auth.error.failed', { detail: (exc as Error).message }))
    } finally {
      setEnvoi(false)
    }
  }

  return (
    <main className="auth-screen">
      <form className="auth-card" onSubmit={soumettre} aria-labelledby="auth-login-title">
        <h1 id="auth-login-title">{t('auth.login.title')}</h1>
        <p>{expiree ? t('auth.expired') : t('auth.login.lead')}</p>
        <Champ id="auth-username" label={t('auth.field.username')} value={identifiant}
               onChange={setIdentifiant} autoComplete="username" />
        <Champ id="auth-password" label={t('auth.field.password')} type="password" value={motDePasse}
               onChange={setMotDePasse} autoComplete="current-password" />
        {erreur && <p className="auth-error" role="alert">{erreur}</p>}
        <button type="submit" className="primary" disabled={envoi}>{t('auth.login.submit')}</button>
        <p className="auth-hint">{t('auth.login.forgot')}</p>
      </form>
    </main>
  )
}

/** Bouton de déconnexion de la barre du haut ; rien hors d'une session. */
export function BoutonDeconnexion() {
  const { t } = useLang()
  const session = useSession()
  if (!session?.utilisateur) return null
  return (
    <button type="button" className="chip auth-logout"
            title={t('auth.connectedAs', { name: session.utilisateur.identifiant })}
            onClick={() => { void session.deconnexion() }}>
      {t('auth.logout')}
    </button>
  )
}
