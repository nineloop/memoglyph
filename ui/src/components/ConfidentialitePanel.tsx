import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'

import { getCloudUltra, getConfig, setCloudUltra, type ReglageCloudUltra } from '../api'
import { cleSelonFaits } from '../lib/messages'
import { annoncerCloudUltra } from '../lib/cloudUltra'
import { useLang } from '../lib/langContext'
import { useSession } from './AuthGate'

/**
 * Vue « Confidentialité » : le réglage du compte connecté qui autorise un
 * fournisseur cloud pour SES documents ultra-confidentiels. Inactif par défaut.
 * Activer demande une confirmation ; désactiver, non, et vaut aussitôt (le
 * serveur relit le réglage à chaque envoi). Le serveur ne règle que le compte de
 * la session : ni un autre compte, ni un jeton d'appareil.
 */
export function ConfidentialitePanel() {
  const { t, lang } = useLang()
  const [reglage, setReglage] = useState<ReglageCloudUltra | null>(null)
  const [confirmer, setConfirmer] = useState(false)
  const [enCours, setEnCours] = useState(false)
  const [erreur, setErreur] = useState('')
  const [avis, setAvis] = useState('')
  // Option des faits de la réunion (`/api/config`, `faits`) : coupée, le texte ne les cite pas.
  const [faits, setFaits] = useState(false)
  useEffect(() => {
    let courant = true
    Promise.resolve().then(() => getConfig())
      .then((config) => { if (courant) setFaits(config?.faits === true) })
      .catch(() => { /* défaut : option coupée */ })
    return () => { courant = false }
  }, [])

  useEffect(() => {
    getCloudUltra().then(setReglage).catch((exc: Error) => setErreur(exc.message))
  }, [])

  const changer = useCallback(async (autorise: boolean) => {
    setEnCours(true)
    setErreur('')
    setAvis('')
    try {
      const lu = await setCloudUltra(autorise)
      setReglage(lu)
      setConfirmer(false)
      annoncerCloudUltra(lu.autorise)
      setAvis(t(lu.autorise ? 'privacy.enabled' : 'privacy.disabled'))
    } catch (exc) {
      setErreur(t('auth.error.failed', { detail: (exc as Error).message }))
    } finally {
      setEnCours(false)
    }
  }, [t])

  const date = (valeur: string | null) =>
    valeur ? new Date(valeur).toLocaleString(lang === 'fr' ? 'fr-FR' : 'en-GB') : ''

  return (
    <section className="accounts-panel" aria-labelledby="privacy-title">
      <h2 id="privacy-title">{t('privacy.title')}</h2>
      <p>{t('privacy.lead')}</p>
      <h3>{t('privacy.opens.title')}</h3>
      <p>{t(cleSelonFaits('privacy.opens', faits))}</p>
      <h3>{t('privacy.keeps.title')}</h3>
      <ul className="privacy-list">
        <li>{t('privacy.keeps.own')}</li>
        <li>{t('privacy.keeps.sharing')}</li>
        <li>{t('privacy.keeps.local')}</li>
        <li>{t('privacy.keeps.immediate')}</li>
        <li>{t('privacy.keeps.trace')}</li>
      </ul>
      {reglage && (
        <p role="status" aria-live="polite">
          <strong>
            {reglage.autorise
              ? t('privacy.state.on', { date: date(reglage.modifie_le) })
              : t('privacy.state.off')}
          </strong>
        </p>
      )}
      {reglage && !reglage.autorise && !confirmer && (
        <button type="button" className="btn" disabled={enCours} onClick={() => setConfirmer(true)}>
          {t('privacy.enable')}
        </button>
      )}
      {reglage && !reglage.autorise && confirmer && (
        <div className="devices-secret" role="alertdialog" aria-labelledby="privacy-confirm-title">
          <h3 id="privacy-confirm-title">{t('privacy.confirm.title')}</h3>
          <p>{t('privacy.confirm.body')}</p>
          <div className="row">
            <button type="button" className="btn danger-subtle" disabled={enCours}
                    onClick={() => { void changer(true) }}>
              {t('privacy.confirm.yes')}
            </button>{' '}
            <button type="button" className="btn" disabled={enCours} onClick={() => setConfirmer(false)}>
              {t('privacy.confirm.no')}
            </button>
          </div>
        </div>
      )}
      {reglage?.autorise && (
        <button type="button" className="btn" disabled={enCours} onClick={() => { void changer(false) }}>
          {t('privacy.disable')}
        </button>
      )}
      {erreur && <p className="auth-error" role="alert">{erreur}</p>}
      {avis && <p role="status">{avis}</p>}
    </section>
  )
}

/** Bouton « Confidentialité » de la barre du haut, pour tout compte connecté. */
export function BoutonConfidentialite() {
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
        {t('privacy.open')}
      </button>
      {/* Rendu dans `document.body`, comme « Appareils » : la barre du haut crée
          un contexte d'empilement qui piégerait un calque en position fixe. */}
      {ouvert && createPortal(
        <div className="accounts-overlay" role="dialog" aria-modal="true" aria-labelledby="privacy-title">
          <div className="accounts-dialog">
            <button type="button" className="chip accounts-close" onClick={() => setOuvert(false)}>
              {t('privacy.close')}
            </button>
            <ConfidentialitePanel />
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}
