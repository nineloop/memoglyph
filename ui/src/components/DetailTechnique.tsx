import { useLang } from '../lib/langContext'

/**
 * Le détail technique d'une erreur (le texte de l'exception ou du serveur) : replié, sous un titre traduit, et **seulement
 * pour un administrateur**. Pour tout autre compte le détail n'est pas dans le DOM : un bloc replié le laisserait dans la page.
 * C'est une restriction d'affichage ; la réponse de l'API ne change pas.
 */
export function DetailTechnique({ detail, admin, titre }: { detail: string; admin: boolean; titre?: string }) {
  const { t } = useLang()
  if (!admin || !detail) return null
  return (
    <details className="detail-technique traitement-echec-detail">
      <summary>{titre ?? t('maint.error.technicalDetail')}</summary>
      <code>{detail}</code>
    </details>
  )
}
