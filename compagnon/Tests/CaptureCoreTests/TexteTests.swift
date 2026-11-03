import CaptureCore
import Foundation
import XCTest

final class TexteTests: XCTestCase {
    private func marqueurs(_ s: String) -> Set<String> {
        let re = try! NSRegularExpression(pattern: #"\{[a-z]+\}"#)
        return Set(re.matches(in: s, range: NSRange(s.startIndex..., in: s)).compactMap { Range($0.range, in: s).map { String(s[$0]) } })
    }

    func testMemesClesEnFrancaisEtEnAnglais() {
        let fr = Set(Texte.table(.fr).keys), en = Set(Texte.table(.en).keys)
        XCTAssertEqual(fr, en, "clés manquantes : \(fr.symmetricDifference(en).sorted())")
        // Chaque clé de l'énumération a sa phrase, et il n'y a pas de phrase sans clé.
        XCTAssertEqual(fr, Set(Cle.allCases.map(\.rawValue)))
        for cle in Cle.allCases {
            for langue in Langue.allCases {
                let phrase = Texte.table(langue)[cle.rawValue] ?? ""
                XCTAssertFalse(phrase.isEmpty, "\(cle) en \(langue)")
            }
            // Les mêmes paramètres dans les deux langues : aucun ne se perd à la traduction.
            XCTAssertEqual(marqueurs(Texte.table(.fr)[cle.rawValue]!), marqueurs(Texte.table(.en)[cle.rawValue]!), "\(cle)")
        }
    }

    func testLaLangueSuitLeSysteme() {
        XCTAssertEqual(Langue.depuis(preferees: ["fr-FR", "en-US"]), .fr)
        XCTAssertEqual(Langue.depuis(preferees: ["fr"]), .fr)
        XCTAssertEqual(Langue.depuis(preferees: ["fr_CA"]), .fr)
        XCTAssertEqual(Langue.depuis(preferees: ["en-GB", "fr-FR"]), .en)
        XCTAssertEqual(Langue.depuis(preferees: ["de-DE"]), .en, "langue de repli")
        XCTAssertEqual(Langue.depuis(preferees: []), .en)
        XCTAssertEqual(Langue.depuis(preferees: ["frisian"]), .en, "pas un simple préfixe « fr »")
    }

    func testTableDesCodesDeMessage() {
        let attendu: [(String?, String)] = [
            ("step.audio.prepare", "Préparation de l'audio…"),
            ("step.audio.ready", "Préparation de l'audio…"),
            ("step.transcription", "Transcription en cours…"),
            ("step.transcription.mistral.done", "Transcription en cours…"),
            ("step.model.loading", "Transcription en cours…"),
            ("step.segments.format", "Transcription en cours…"),
            ("step.diarization.analyzing", "Identification des voix…"),
            ("step.voiceprints.after_native_diarization", "Identification des voix…"),
            ("step.profiling.running", "Analyse de la réunion…"),
            ("step.report", "Compte-rendu en cours…"),
            ("step.report.extract", "Compte-rendu en cours…"),
            ("step.report.organize", "Compte-rendu en cours…"),
            ("step.report.write", "Compte-rendu en cours…"),
            ("step.markdown", "Écriture de la réunion…"),
            ("step.done", "Écriture de la réunion…"),
            ("code.inconnu", "Traitement en cours…"),
            ("step.reportage", "Traitement en cours…"),
            (nil, "Traitement en cours…"),
            ("", "Traitement en cours…"),
        ]
        for (code, phrase) in attendu { XCTAssertEqual(Texte.etape(code: code, .fr), phrase, "\(code ?? "nil")") }
        XCTAssertEqual(Texte.etape(code: "step.report.write", .en), "Writing the report…")
        XCTAssertEqual(Texte.etape(code: "step.report.write", enAttente: true, .fr), "En attente d'une place…")
    }

    func testLeTexteLibreDuServeurNEstJamaisAffiche() {
        let etat = EtatCapture.traitement(progression: 0.4, code: nil, enAttente: false)
        // Même si le serveur envoie « Profiling with qwen3:9b… », seul le code compte.
        XCTAssertEqual(Texte.ligne(etat, .fr), "Traitement en cours… 40 %")
        XCTAssertEqual(Texte.ligne(.traitement(progression: 0.8, code: "step.report.write", enAttente: false), .fr),
                       "Compte-rendu en cours… 80 %")
        XCTAssertEqual(Texte.ligne(.traitement(progression: 0.8, code: "step.report.write", enAttente: false), .en),
                       "Writing the report… 80%")
    }

    func testEnAttenteNTraitementsEnCours() {
        XCTAssertEqual(Texte.enAttente(actifs: 1, .fr), "en attente : 1 traitement en cours")
        XCTAssertEqual(Texte.enAttente(actifs: 2, .fr), "en attente : 2 traitements en cours")
        XCTAssertEqual(Texte.enAttente(actifs: 2, .en), "waiting: 2 jobs running")
        XCTAssertEqual(Texte.ligne(.attente(essai: 1, dans: 15, cause: .occupe), actifs: 2, .fr),
                       "en attente : 2 traitements en cours · nouvel essai dans 15 s")
        XCTAssertEqual(Texte.ligne(.attente(essai: 5, dans: 300, cause: .occupe), actifs: 0, .fr),
                       "En attente d'une place… · nouvel essai dans 5 min")
        XCTAssertEqual(Texte.duree(15), "15 s"); XCTAssertEqual(Texte.duree(60), "1 min"); XCTAssertEqual(Texte.duree(120), "2 min")
    }

    func testLaPastilleADuLibelleEtUneForme() {
        for langue in Langue.allCases {
            let formes = [Texte.Pastille.liee: "●", .horsLigne: "○", .verification: "◌", .nonRelie: "○"]
            for (etat, forme) in formes {
                let t = Texte.pastille(etat, langue)
                XCTAssertTrue(t.hasPrefix(forme), t)
                XCTAssertGreaterThan(t.count, 3, "un libellé, pas la seule forme : \(t)")
            }
        }
        XCTAssertEqual(Texte.pastille(.liee, .fr), "● Liée")
        XCTAssertEqual(Texte.pastille(.horsLigne, .fr), "○ Hors ligne")
        XCTAssertEqual(Texte.pastille(.verification, .fr), "◌ Vérification…")
        XCTAssertEqual(Texte.pastille(.liee, .en), "● Linked")
        // Les trois états distincts se distinguent sans la couleur : formes et libellés différents.
        XCTAssertEqual(Set([Texte.Pastille.liee, .horsLigne, .verification].map { Texte.pastille($0, .fr) }).count, 3)
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: false, joignable: true), .nonRelie)
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: true, joignable: true), .liee)
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: true, joignable: false), .horsLigne)
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: true, joignable: nil), .verification)
    }

    func testAucunIdentifiantNiTermeTechniqueDansUneLigneAffichee() {
        let jeton = "mgj_" + String(repeating: "Ab1-_", count: 8)
        let issues: [Issue] = [
            .jetonRefuse, .introuvable, .injoignable("Connexion refusée \(jeton)"),
            .refuse(code: 413, raison: "Fichier trop volumineux job_id=ab12 \(jeton)"),
            .refuse(code: 500, raison: "Traceback"), .dejaRecue(reunion: "r.md", travail: "ab12cd34ef56"),
        ]
        var etats: [EtatCapture] = [.sansLiaison, .envoi, .recue, .prete(avertissements: 0), .prete(avertissements: 2),
                                    .echecTraitement("Traceback job_id=ab12cd34ef56 \(jeton)"),
                                    .traitement(progression: 0.5, code: "step.report", enAttente: false),
                                    .traitement(progression: 0, code: nil, enAttente: true)]
        for issue in issues {
            etats += [.nonEnvoyee(issue), .suiviInterrompu(issue)]
        }
        for cause in [CauseAttente.occupe, .injoignable, .incident] { etats.append(.attente(essai: 2, dans: 30, cause: cause)) }
        let interdits = [#"mgj_"#, #"[Hh][Tt][Tt][Pp]"#, #"job_id"#, #"\b[0-9a-f]{12}\b"#, #"Traceback"#, #"\b(4|5)[0-9]{2}\b"#,
                         #"UUID"#, #"JSON"#, #"API"#, #"\{"#]
        for langue in Langue.allCases {
            for etat in etats {
                let ligne = Texte.ligne(etat, actifs: 3, langue)
                for motif in interdits {
                    XCTAssertNil(ligne.range(of: motif, options: .regularExpression), "« \(ligne) » contient \(motif)")
                }
            }
        }
    }

    func testConfirmationRangeeEtAttenteEventuelle() {
        XCTAssertEqual(Texte.confirmation(projet: "Équipe produit", autresEnCours: 0, .fr), "Rangé dans Équipe produit.")
        XCTAssertEqual(Texte.confirmation(projet: "Équipe produit", autresEnCours: 1, .fr),
                       "Rangé dans Équipe produit. En attente : un traitement est déjà en cours.")
        XCTAssertEqual(Texte.confirmation(projet: nil, autresEnCours: 0, .fr), "Rangé dans projet par défaut.")
        XCTAssertEqual(Texte.confirmation(projet: "Product team", autresEnCours: 0, .en), "Saved to Product team.")
        XCTAssertEqual(Texte.phrase(.actionSuivre, .fr), "Suivre")
        XCTAssertEqual(Texte.phrase(.actionSuivre, .en), "Follow")
    }

    func testCaptureNonEnvoyeeDitLaRaisonEnUnePhrase() {
        XCTAssertEqual(Texte.ligne(.nonEnvoyee(.jetonRefuse), .fr),
                       "Capture non envoyée : le jeton est refusé, reliez à nouveau le compagnon")
        XCTAssertEqual(Texte.ligne(.nonEnvoyee(.injoignable("x")), .en), "Capture not sent: Memoglyph is unreachable")
    }

    func testLesAlertesLesRappelsEtLeMenuVisioExistentDansLesDeuxLangues() {
        // Le français est celui d'origine, mot pour mot : la ligne de commande et les tests d'avant ne bougent pas.
        for alerte in [Alerte.microDecroche, .saturation, .echo, .peripheriqueChange, .processusDisparu] {
            XCTAssertEqual(alerte.texte(.fr), alerte.texte)
            XCTAssertNotEqual(alerte.texte(.en), alerte.texte(.fr))
        }
        for visio in [true, false] {
            XCTAssertEqual(Rappels.auDemarrage(visio: visio, .fr), Rappels.auDemarrage(visio: visio))
            XCTAssertEqual(Rappels.auDemarrage(visio: visio, .en).count, Rappels.auDemarrage(visio: visio).count)
            XCTAssertNotEqual(Rappels.auDemarrage(visio: visio, .en), Rappels.auDemarrage(visio: visio))
        }
        XCTAssertEqual(ChoixVisio.toutLeSon(.fr), ChoixVisio.toutLeSon)
        XCTAssertEqual(ChoixVisio.aucunAppel(.fr), ChoixVisio.aucunAppel)
        let appel = LigneVisio(nom: "zoom.us", cible: "us.zoom.xos", joue: true, enAppel: true)
        XCTAssertEqual(appel.libelle(.fr), appel.libelle)
        XCTAssertEqual(appel.libelle(.en), "Call in progress: zoom.us")
        let web = LigneVisio(nom: ChoixVisio.nomWebkit, cible: ChoixVisio.webkit, joue: false, enAppel: false)
        XCTAssertEqual(web.libelle(.fr), web.libelle)
        XCTAssertEqual(web.libelle(.en), "Safari (web page audio)")
    }

    func testAucuneBoiteDeDialogueSansSesDeuxLangues() {
        for cle in Cle.allCases where cle.rawValue.hasPrefix("dlg") && cle != .dlgOK {   // « OK » est le même mot
            XCTAssertNotEqual(Texte.phrase(cle, .fr), Texte.phrase(cle, .en), "\(cle) identique : traduction oubliée ?")
        }
    }

    // MARK: - Corrections issues de l'essai du 3/11

    func testLeNomDuProjetEstCeluiDuTraitementEtNonClasseSeTraduit() {
        let jeton = "mgj_" + String(repeating: "Ab1-_", count: 8)
        let corps = try! JSONSerialization.data(withJSONObject: [
            "status": "running", "progress": 0.1, "message": "", "message_code": "step.audio.prepare",
            "projet_nom": "Non classé", "projet_id": "P0"])
        guard case .enCours(_, _, _, _, let nom) = ClientMemoglyph.lireTravail(statut: 200, corps: corps, erreur: nil, jeton: jeton)
        else { return XCTFail("attendu : en cours") }
        XCTAssertEqual(nom, "Non classé")
        XCTAssertEqual(Texte.confirmation(projet: Texte.nomDeProjet(nom!, .en), autresEnCours: 0, .en), "Saved to Unclassified.")
        XCTAssertEqual(Texte.confirmation(projet: Texte.nomDeProjet(nom!, .fr), autresEnCours: 0, .fr), "Rangé dans Non classé.")
        let fini = try! JSONSerialization.data(withJSONObject: ["status": "done", "projet_nom": "Équipe",
                                                                "result": ["name": "r.md"]])
        guard case .fini(_, _, _, _, let nomFini) = ClientMemoglyph.lireTravail(statut: 200, corps: fini, erreur: nil, jeton: jeton)
        else { return XCTFail("attendu : fini") }
        XCTAssertEqual(nomFini, "Équipe")
        // Sans nom (serveur plus ancien) : rien d'inventé.
        let sans = try! JSONSerialization.data(withJSONObject: ["status": "running", "progress": 0])
        guard case .enCours(_, _, _, _, let aucun) = ClientMemoglyph.lireTravail(statut: 200, corps: sans, erreur: nil, jeton: jeton)
        else { return XCTFail() }
        XCTAssertNil(aucun)
    }

    func testNonClasseSeTraduitDansLesListesDeProjets() {
        let corps = try! JSONSerialization.data(withJSONObject: [
            ["id": "p1", "project": "Non classé", "kind": "inbox"], ["id": "p2", "project": "Équipe", "kind": "project"]])
        let projets = try! ClientMemoglyph.lireProjets(statut: 200, corps: corps, erreur: nil, jeton: nil).get()
        XCTAssertEqual(projets.map(\.kind), ["inbox", "project"])
        XCTAssertEqual(projets.map { Texte.nomDeProjet($0.nom, kind: $0.kind, .en) }, ["Unclassified", "Équipe"])
        XCTAssertEqual(projets.map { Texte.nomDeProjet($0.nom, kind: $0.kind, .fr) }, ["Non classé", "Équipe"])
        // Un projet que l'utilisateur a nommé « Unclassified » n'est pas du système, mais reste lisible tel quel en anglais ;
        // un projet « Inbox » n'est jamais touché.
        XCTAssertEqual(Texte.nomDeProjet("Inbox", kind: "project", .en), "Inbox")
        XCTAssertEqual(Texte.nomDeProjet("Non classé", .en), "Unclassified", "sans le kind, par la constante")
    }

    func testLaTypographieSuitLaLangue() {
        XCTAssertEqual(Texte.etiquette("visio-1.wav", "prête", .fr), "visio-1.wav : prête")
        XCTAssertEqual(Texte.etiquette("visio-1.wav", "ready", .en), "visio-1.wav: ready")
        XCTAssertEqual(Texte.etiquette("visio-1.wav", Texte.ligne(.prete(avertissements: 0), .en), .en), "visio-1.wav: ready")
        // En anglais, aucune espace avant la ponctuation double, aucun guillemet français.
        for (cle, phrase) in Texte.table(.en) {
            for motif in [" :", " ;", " ?", " !", "«", "»"] {
                XCTAssertFalse(phrase.contains(motif), "« \(phrase) » (\(cle)) contient « \(motif) »")
            }
        }
        for etat: EtatCapture in [.envoi, .recue, .attente(essai: 1, dans: 15, cause: .occupe), .nonEnvoyee(.jetonRefuse),
                                  .traitement(progression: 0.3, code: "step.report", enAttente: false)] {
            let ligne = Texte.etiquette("a.wav", Texte.ligne(etat, actifs: 2, .en), .en)
            XCTAssertFalse(ligne.contains(" :"), ligne)
        }
    }

    func testOublierLaLiaisonAUneEtiquetteDansLesDeuxLangues() {
        XCTAssertEqual(Texte.phrase(.menuOublier, .fr), "Oublier la liaison")
        XCTAssertEqual(Texte.phrase(.menuOublier, .en), "Forget the link")
    }

    func testUnJetonRefuseChangeLaPastilleJusquAUneNouvelleLiaison() {
        let jeton = "mgj_" + String(repeating: "Ab1-_", count: 8)
        // Memoglyph répond à la santé, mais le jeton est refusé (révoqué) : la pastille ne dit plus « liée ».
        let issue = ClientMemoglyph.lireEnvoi(statut: 401, corps: nil, erreur: nil, jeton: jeton)
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: true, joignable: true, derniereIssue: issue), .refusee)
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: true, joignable: nil, derniereIssue: .jetonRefuse), .refusee)
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: true, joignable: true, derniereIssue: nil), .liee, "une nouvelle liaison la lève")
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: true, joignable: false, derniereIssue: .injoignable("réseau")), .horsLigne)
        XCTAssertEqual(Texte.etatDeLaLiaison(lie: false, joignable: true, derniereIssue: .jetonRefuse), .nonRelie)
        XCTAssertEqual(Texte.pastille(.refusee, .fr), "⚠︎ Liaison refusée")
        XCTAssertEqual(Texte.pastille(.refusee, .en), "⚠︎ Link refused")
        // Quatre états distincts, chacun avec un libellé ET une forme qui ne sont pas ceux de « liée ».
        let etats: [Texte.Pastille] = [.liee, .horsLigne, .verification, .refusee]
        XCTAssertEqual(Set(etats.map { Texte.pastille($0, .fr) }).count, 4)
        XCTAssertFalse(Texte.pastille(.refusee, .fr).hasPrefix("●"))
    }
}
