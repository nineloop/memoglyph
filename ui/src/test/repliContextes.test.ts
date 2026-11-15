import { expect, it } from 'vitest'

import { CATALOGUES } from '../lib/messages'

// Les phrases d'un échec que l'écran dit quand le serveur n'a pas donné de code que le catalogue connaît. Aucune ne porte
// d'espace réservé : le texte brut d'une exception n'y entre jamais.
const REPLIS: Array<[string, string, string]> = [
  ['library.moveFailed', 'Le déplacement n’a pas abouti.', 'The move did not complete.'],
  ['library.archiveIncomplete', 'L’archivage n’a pas abouti entièrement.', 'Archiving was not completed in full.'],
  ['meeting.error.archiveFailed', 'L’archivage n’a pas abouti.', 'Archiving did not complete.'],
  ['meeting.error.sensitivityNotSaved', 'Sensibilité non enregistrée.', 'Sensitivity not saved.'],
  ['reader.openError', 'Ce transcript n’a pas pu être ouvert.', 'This transcript could not be opened.'],
  ['trash.restore.failed', 'La restauration n’a pas abouti.', 'The restore did not complete.'],
  ['trash.delete.failed', 'La suppression n’a pas abouti : l’élément conservé reste visible et la tentative peut être relancée.',
    'The deletion did not complete: the kept item stays visible and the attempt can be retried.'],
  ['trash.empty.failed', 'Nettoyage partiel : les éléments conservés restent visibles et la tentative peut être relancée.',
    'Partial cleanup: the kept items stay visible and the attempt can be retried.'],
  ['repli.voixRenommer', 'Le nom de la voix n’a pas pu être enregistré.', 'The voice name could not be saved.'],
  ['repli.voixFusionner', 'La fusion des voix n’a pas abouti.', 'The voices could not be merged.'],
  ['repli.maintenanceCharger', 'L’état de la maintenance n’a pas pu être chargé.', 'The maintenance status could not be loaded.'],
  ['repli.maintenanceOperation', 'L’opération n’a pas abouti.', 'The operation did not complete.'],
  ['maint.error.technicalDetail', 'Détail technique', 'Technical detail'],
  ['search.failed', 'La recherche n’a pas pu aboutir.', 'The search could not be completed.'],
  ['repli.assistant', 'L’Assistant n’a pas pu répondre.', 'The Assistant could not answer.'],
  ['repli.envoi', 'L’envoi n’a pas pu aboutir. Réessayez dans un moment.', 'The upload could not be completed. Try again in a moment.'],
  ['app.backendDown', 'Le serveur est injoignable.', 'The server cannot be reached.'],
  ['app.project.createFailed', 'Le projet n’a pas pu être créé.', 'The project could not be created.'],
  ['regenerer.refused.other', 'La régénération n’a pas pu démarrer.', 'Regeneration could not start.'],
  ['meeting.facts.error', 'Faits illisibles.', 'Facts unreadable.'],
  ['meeting.error.reportUnreadable', 'Compte rendu illisible.', 'Report unreadable.'],
]

it('chaque phrase de repli existe dans les deux langues, sans espace réservé de texte brut', () => {
  for (const [cle, fr, en] of REPLIS) {
    expect(CATALOGUES.fr[cle as keyof typeof CATALOGUES.fr], cle).toBe(fr)
    expect(CATALOGUES.en[cle as keyof typeof CATALOGUES.en], cle).toBe(en)
  }
})

it('la clé morte du détail d’un échec de traitement a disparu ; le titre du détail replié reste', () => {
  expect('traitements.failed.detail' in CATALOGUES.fr).toBe(false)
  expect('traitements.failed.detail' in CATALOGUES.en).toBe(false)
  expect(CATALOGUES.fr['traitements.failed.tech']).toBe('Détail technique')
  expect(CATALOGUES.en['traitements.failed.tech']).toBe('Technical detail')
})

it('la « Cause technique » de la Recherche n’a plus de phrase', () => {
  expect('search.unavailable.cause' in CATALOGUES.fr).toBe(false)
  expect('search.unavailable.cause' in CATALOGUES.en).toBe(false)
})
