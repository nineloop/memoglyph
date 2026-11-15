import { useEffect, useId, useMemo, useState } from 'react'

import type { AppConfig } from '../api'
import { LIBELLES, estEnLigne, estInterdit, estPret, fournisseursConnus, type FournisseurConnu } from '../lib/fournisseurs'
import { useLang } from '../lib/langContext'
import { Icon } from './Icon'

type Props = {
  config: Pick<AppConfig, 'llm_etats' | 'llm_providers' | 'llm_ready' | 'local_providers' | 'private_cloud_providers' | 'llm_models'>
  /** Le fournisseur choisi (identifiant du registre). */
  valeur: string
  onChange: (fournisseur: string) => void
  /** Le texte de la légende du groupe. */
  legende: string
  /** Le nom du groupe de boutons radio (unique dans la page). */
  nom: string
  /** Le document est ultra-confidentiel : « En ligne » est désactivé, sauf autorisation du propriétaire. */
  ultra?: boolean
  /** Le propriétaire autorise l'envoi en ligne de son ultra-confidentiel. */
  cloudUltra?: boolean
  desactive?: boolean
  /** « En ligne » est ouvert dès l'affichage (« Régénérer » : on y vient pour changer de fournisseur). Sinon replié, sauf si le choix est en ligne. */
  enLigneOuvert?: boolean
}

/**
 * LE sélecteur de fournisseur de l'interface (Transcrire et Régénérer) : « Local » d'abord, puis « En ligne » repliable avec
 * chaque fournisseur et son état. Un fournisseur non configuré s'affiche en grisé, sans bouton, avec une aide d'une ligne qui ne
 * nomme aucune variable. L'écran REFLÈTE la règle d'envoi (ultra-confidentiel : local, sauf autorisation ; un modèle gratuit
 * jamais) ; le serveur garde le dernier mot.
 */
export function SelecteurFournisseur({ config, valeur, onChange, legende, nom, ultra = false, cloudUltra = false, desactive = false, enLigneOuvert = false }: Props) {
  const { t } = useLang()
  const idLegende = useId()
  const idRegion = useId()
  const idLocal = useId()
  const tous = useMemo(() => fournisseursConnus(config), [config])
  const locaux = tous.filter((f) => !estEnLigne(f))
  const enLigne = tous.filter(estEnLigne)
  const choisi = tous.find((f) => f.id === valeur)
  const [ouvert, setOuvert] = useState(() => enLigneOuvert || Boolean(choisi && estEnLigne(choisi)))
  // Un choix en ligne fait du dehors (les réglages du compte arrivent après l'affichage) se dévoile : le fournisseur coché ne reste
  // jamais caché dans une section repliée.
  const choisiEnLigne = Boolean(choisi && estEnLigne(choisi))
  useEffect(() => { if (choisiEnLigne) setOuvert(true) }, [choisiEnLigne, valeur])
  const libelle = (id: string) => (LIBELLES[id] ? t(LIBELLES[id]) : id)
  const enLigneRefuse = ultra && !cloudUltra
  // L'aide d'une ligne n'accompagne chaque fournisseur non configuré que s'il y en a de prêts à côté. Si AUCUN fournisseur en ligne
  // n'est prêt, la même phrase sous chaque ligne serait répétée à l'identique : l'écran qui accueille le sélecteur la dit une fois.
  const aideParLigne = enLigne.some(estPret)

  /** Pourquoi un fournisseur prêt ne peut pas être choisi pour ce document, en texte. */
  const raison = (f: FournisseurConnu): string => {
    if (!estInterdit(f, ultra, cloudUltra)) return ''
    return f.etat === 'gratuit' ? t('fournisseur.gratuitUltra') : t('regenerer.notAllowed')
  }

  const ligne = (f: FournisseurConnu) => {
    const modele = config.llm_models?.[f.id]
    if (!estPret(f)) {
      return (
        <li key={f.id} className="selecteur-indispo" aria-disabled="true">
          <span>
            <strong>{libelle(f.id)}</strong>
            <span className="selecteur-etat">{t(f.lieu === 'local' ? 'fournisseur.etat.indisponible' : 'fournisseur.etat.nonConfigure')}</span>
          </span>
          {f.lieu !== 'local' && aideParLigne && <small>{t('fournisseur.aide.nonConfigure')}</small>}
        </li>
      )
    }
    const pourquoi = raison(f)
    const interdit = Boolean(pourquoi)
    return (
      <li key={f.id}>
        <label className={`regenerer-option ${interdit ? 'interdit' : ''}`}>
          <input type="radio" name={nom} value={f.id} checked={valeur === f.id} onChange={() => onChange(f.id)}
                 disabled={desactive || interdit} aria-describedby={interdit ? `${idLegende}-raison-${f.id}` : undefined} />
          <span>
            <strong>{libelle(f.id)}</strong>
            {modele && <small> · <code>{modele}</code></small>}
            {f.etat === 'gratuit' && <span className="selecteur-etat">{t('fournisseur.etat.gratuit')}</span>}
            {f.lieu === 'cloud_prive' && <span className="selecteur-etat">{t('fournisseur.etat.cloudPrive')}</span>}
            {/* La raison est du TEXTE, lié à l'option : une option désactivée ne reçoit pas le focus. */}
            {pourquoi && <small id={`${idLegende}-raison-${f.id}`} className="regenerer-raison"> — {pourquoi}</small>}
          </span>
        </label>
      </li>
    )
  }

  return (
    <fieldset className="regenerer-choix selecteur-fournisseur" aria-labelledby={idLegende}>
      <legend id={idLegende}>{legende}</legend>
      {/* Les deux lieux sont nommés : « Local » ici (un intitulé, pas un bouton : il ne se replie pas), « En ligne » plus bas. */}
      {locaux.length > 0 && <p id={idLocal} className="selecteur-intitule">{t('fournisseur.lieu.local')}</p>}
      <ul aria-labelledby={locaux.length > 0 ? idLocal : undefined}>{locaux.map(ligne)}</ul>
      {enLigne.length > 0 && (
        <>
          <button type="button" className="selecteur-bascule" aria-expanded={ouvert} aria-controls={idRegion}
                  onClick={() => setOuvert((o) => !o)}>
            <Icon name={ouvert ? 'chevron-up' : 'chevron-down'} />
            {t('fournisseur.enLigne')}
            {enLigneRefuse && <span className="muted"> · {t('fournisseur.localSeul')}</span>}
          </button>
          <ul id={idRegion} hidden={!ouvert}>{ouvert && enLigne.map(ligne)}</ul>
        </>
      )}
    </fieldset>
  )
}
