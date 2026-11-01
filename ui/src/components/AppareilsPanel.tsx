import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'

import { creerJeton, listerJetons, retirerJeton, type JetonAppareil } from '../api'
import { useLang } from '../lib/langContext'
import { useSession } from './AuthGate'

/**
 * Vue « Appareils » : les jetons d'appareil du compte connecté, pour le
 * compagnon de capture. Le secret n'est montré qu'une fois, à la création ; le
 * serveur n'en garde que l'empreinte et ne le rend plus jamais. Chaque compte ne
 * voit et ne retire que ses jetons, vérifié par le serveur.
 */
export function AppareilsPanel() {
  const { t, lang } = useLang()
  const [jetons, setJetons] = useState<JetonAppareil[]>([])
  const [nom, setNom] = useState('')
  const [nouveau, setNouveau] = useState<{ nom: string; secret: string } | null>(null)
  const [erreur, setErreur] = useState('')
  const [avis, setAvis] = useState('')

  const charger = useCallback(() => {
    listerJetons().then(setJetons).catch((exc: Error) => setErreur(exc.message))
  }, [])
  useEffect(() => { charger() }, [charger])

  const date = (valeur: string | null) =>
    valeur ? new Date(valeur).toLocaleString(lang === 'fr' ? 'fr-FR' : 'en-GB') : t('devices.never')

  const creer = async (event: FormEvent) => {
    event.preventDefault()
    setErreur('')
    setAvis('')
    try {
      const { jeton, secret } = await creerJeton(nom)
      setNouveau({ nom: jeton.nom, secret })
      setNom('')
      charger()
    } catch (exc) {
      setErreur(t('auth.error.failed', { detail: (exc as Error).message }))
    }
  }

  const retirer = async (jeton: JetonAppareil) => {
    setErreur('')
    setAvis('')
    try {
      await retirerJeton(jeton.id)
      setAvis(t('devices.revoked', { name: jeton.nom }))
      charger()
    } catch (exc) {
      setErreur(t('auth.error.failed', { detail: (exc as Error).message }))
    }
  }

  return (
    <section className="accounts-panel" aria-labelledby="devices-title">
      <h2 id="devices-title">{t('devices.title')}</h2>
      <p>{t('devices.lead')}</p>
      {jetons.length === 0 ? <p>{t('devices.empty')}</p> : (
        <table className="accounts-table">
          <thead>
            <tr>
              <th>{t('devices.col.name')}</th>
              <th>{t('devices.col.created')}</th>
              <th>{t('devices.col.lastUsed')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {jetons.map((jeton) => (
              <tr key={jeton.id}>
                <td>{jeton.nom}</td>
                <td>{date(jeton.cree_le)}</td>
                <td>{date(jeton.dernier_usage)}</td>
                <td>
                  <button type="button" className="chip" onClick={() => { void retirer(jeton) }}>
                    {t('devices.revoke')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {nouveau && (
        <div className="devices-secret" role="status">
          <h3>{t('devices.secret.title', { name: nouveau.nom })}</h3>
          <p>{t('devices.secret.hint')}</p>
          <label className="field" htmlFor="devices-secret">{t('devices.secret.label')}
            <input id="devices-secret" type="text" readOnly value={nouveau.secret}
                   onFocus={(event) => event.target.select()} />
          </label>
        </div>
      )}
      <form className="accounts-form" onSubmit={creer} aria-label={t('devices.new')}>
        <h3>{t('devices.new')}</h3>
        <label className="field" htmlFor="devices-name">{t('devices.field.name')}
          <input id="devices-name" type="text" required maxLength={64} autoComplete="off" value={nom}
                 onChange={(event) => setNom(event.target.value)} />
        </label>
        <button type="submit" className="primary">{t('devices.create')}</button>
      </form>
      {erreur && <p className="auth-error" role="alert">{erreur}</p>}
      {avis && <p role="status">{avis}</p>}
    </section>
  )
}

/** Bouton « Appareils » de la barre du haut, pour tout compte connecté. */
export function BoutonAppareils() {
  const { t } = useLang()
  const session = useSession()
  const [ouvert, setOuvert] = useState(false)
  useEffect(() => {
    if (!ouvert) return
    const echap = (event: KeyboardEvent) => { if (event.key === 'Escape') setOuvert(false) }
    document.addEventListener('keydown', echap)
    return () => document.removeEventListener('keydown', echap)
  }, [ouvert])
  if (!session?.utilisateur) return null
  return (
    <>
      <button type="button" className="chip auth-logout" onClick={() => setOuvert(true)}>
        {t('devices.open')}
      </button>
      {/* Rendu dans `document.body`, comme « Comptes » : la barre du haut crée
          un contexte d'empilement qui piégerait un calque en position fixe. */}
      {ouvert && createPortal(
        <div className="accounts-overlay" role="dialog" aria-modal="true" aria-labelledby="devices-title">
          <div className="accounts-dialog">
            <button type="button" className="chip accounts-close" onClick={() => setOuvert(false)}>
              {t('devices.close')}
            </button>
            <AppareilsPanel />
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}
