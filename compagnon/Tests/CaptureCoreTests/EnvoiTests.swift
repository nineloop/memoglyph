import CaptureCore
import Foundation
import XCTest

final class EnvoiTests: XCTestCase {
    // Un jeton de test, jamais un vrai : la forme suffit.
    private let jeton = "mgj_" + String(repeating: "Ab1-_", count: 8)
    private let adresse = URL(string: "http://127.0.0.1:8770")!
    private let capture = Capture(fichier: "visio-2026-10-25-08h5320.wav", visio: true, application: "zoom.us")

    private func json(_ o: Any) -> Data { try! JSONSerialization.data(withJSONObject: o) }

    // MARK: - Jeton et adresse

    func testFormeDuJeton() {
        XCTAssertTrue(ClientMemoglyph.jetonValide(jeton))
        XCTAssertFalse(ClientMemoglyph.jetonValide("mgj_court"))
        XCTAssertFalse(ClientMemoglyph.jetonValide("abc_" + String(repeating: "x", count: 30)))
        XCTAssertFalse(ClientMemoglyph.jetonValide("mgj_" + String(repeating: "x", count: 20) + " espace"))
    }

    func testAdresse() {
        XCTAssertEqual(ClientMemoglyph.adresse(" http://127.0.0.1:8770/ "), adresse)
        XCTAssertNil(ClientMemoglyph.adresse("127.0.0.1:8770"))
        XCTAssertNil(ClientMemoglyph.adresse("ftp://hote"))
        XCTAssertNil(ClientMemoglyph.adresse("http://hote/api"))
    }

    // MARK: - Requêtes

    func testSeulesLesRoutesDuCompagnonEtJamaisDeCreationDeProjet() {
        let routes: [Route] = [.sante, .projets, .envoi, .travail("abc123")]
        let vues = routes.map { r -> String in
            let q = ClientMemoglyph.requete(r, adresse: adresse, jeton: jeton)
            return "\(q.httpMethod!) \(q.url!.path)"
        }
        XCTAssertEqual(vues, ["GET /api/health", "GET /api/projects", "POST /api/transcribe", "GET /api/jobs/abc123"])
        XCTAssertFalse(vues.contains("POST /api/projects"))
    }

    func testLeJetonNeVoyageQueDansLEnTeteAuthorization() {
        for route in [Route.projets, .envoi, .travail("j1")] {
            let q = ClientMemoglyph.requete(route, adresse: adresse, jeton: jeton)
            XCTAssertEqual(q.value(forHTTPHeaderField: "Authorization"), "Bearer \(jeton)")
            XCTAssertFalse(q.url!.absoluteString.contains(jeton))
            for (nom, valeur) in q.allHTTPHeaderFields ?? [:] where nom != "Authorization" {
                XCTAssertFalse(valeur.contains(jeton))
            }
        }
        XCTAssertNil(ClientMemoglyph.requete(.sante, adresse: adresse, jeton: jeton)
            .value(forHTTPHeaderField: "Authorization"), "la santé est publique : pas de jeton")
        let (entete, pied) = ClientMemoglyph.multipart(capture, projet: "p1", frontiere: "F")
        XCTAssertFalse(String(decoding: entete + pied, as: UTF8.self).contains("mgj_"))
    }

    func testCorpsMultipartAvecProvenanceEtProjet() {
        let (entete, pied) = ClientMemoglyph.multipart(capture, projet: "p1", frontiere: "F")
        let texte = String(decoding: entete, as: UTF8.self)
        XCTAssertTrue(texte.contains("name=\"capture_mode\"\r\n\r\nvisio\r\n"))
        XCTAssertTrue(texte.contains("name=\"capture_application\"\r\n\r\nzoom.us\r\n"))
        XCTAssertTrue(texte.contains("name=\"destination_project_id\"\r\n\r\np1\r\n"))
        XCTAssertTrue(texte.hasSuffix("filename=\"visio-2026-10-25-08h5320.wav\"\r\nContent-Type: audio/wav\r\n\r\n"))
        XCTAssertEqual(String(decoding: pied, as: UTF8.self), "\r\n--F--\r\n")
    }

    func testSansChoixDeProjetLeServeurPrendLeProjetParDefaut() {
        let replay = Capture(fichier: "r.wav", visio: false, application: String(repeating: "a", count: 120))
        let texte = String(decoding: ClientMemoglyph.multipart(replay, projet: nil, frontiere: "F").entete, as: UTF8.self)
        XCTAssertFalse(texte.contains("destination_project_id"))
        XCTAssertTrue(texte.contains("\r\n\r\nreplay\r\n"))
        XCTAssertTrue(texte.contains("\r\n\r\n" + String(repeating: "a", count: 80) + "\r\n"), "borné à 80 caractères")
    }

    // MARK: - Réponses

    func testReponsesALEnvoi() {
        XCTAssertEqual(ClientMemoglyph.lireEnvoi(statut: 200, corps: json(["job_id": "j1"]), erreur: nil, jeton: jeton),
                       .accepte(travail: "j1"))
        XCTAssertEqual(ClientMemoglyph.lireEnvoi(statut: 401, corps: nil, erreur: nil, jeton: jeton), .jetonRefuse)
        XCTAssertEqual(ClientMemoglyph.lireEnvoi(statut: 404, corps: nil, erreur: nil, jeton: jeton), .introuvable)
        XCTAssertEqual(ClientMemoglyph.lireEnvoi(statut: 413, corps: json(["detail": "Fichier trop volumineux"]),
                                                 erreur: nil, jeton: jeton),
                       .refuse(code: 413, raison: "Fichier trop volumineux"))
        XCTAssertEqual(ClientMemoglyph.lireEnvoi(statut: nil, corps: nil, erreur: "Connexion refusée", jeton: jeton),
                       .injoignable("Connexion refusée"))
    }

    func testLeJetonNApparaitDansAucunMessage() {
        let piege = "jeton \(jeton) et un autre " + "mgj_" + String(repeating: "Z", count: 20)
        let issues: [Issue] = [
            ClientMemoglyph.lireEnvoi(statut: 400, corps: json(["detail": piege]), erreur: nil, jeton: jeton),
            ClientMemoglyph.lireEnvoi(statut: nil, corps: nil, erreur: piege, jeton: jeton),
            .jetonRefuse, .introuvable,
        ]
        var textes = issues.map(\.texte)
        if case .echec(let t) = ClientMemoglyph.lireTravail(statut: 200, corps: json(["status": "error", "error": piege]),
                                                            erreur: nil, jeton: jeton) { textes.append(t) }
        if case .fini(_, let a, _, _, _) = ClientMemoglyph.lireTravail(
            statut: 200, corps: json(["status": "done", "result": ["name": "r.md", "warnings": [piege]]]),
            erreur: nil, jeton: jeton) { textes += a }
        XCTAssertEqual(textes.count, 6)
        for t in textes {
            XCTAssertFalse(t.contains(jeton), t)
            XCTAssertNil(t.range(of: #"mgj_[A-Za-z0-9_\-]{4,}"#, options: .regularExpression), t)
        }
    }

    func testSuiviDuTravail() {
        XCTAssertEqual(ClientMemoglyph.lireTravail(statut: 200, corps: json(["status": "running", "progress": 0.4,
                                                                             "message": "Transcription"]),
                                                   erreur: nil, jeton: jeton),
                       .enCours(progression: 0.4, message: "Transcription"))
        XCTAssertEqual(ClientMemoglyph.lireTravail(statut: 200, corps: json(["status": "done", "result": ["name": "r.md"]]),
                                                   erreur: nil, jeton: jeton),
                       .fini(reunion: "r.md", avertissements: []))
        XCTAssertEqual(ClientMemoglyph.lireTravail(statut: 404, corps: nil, erreur: nil, jeton: jeton), .perdu(.introuvable))
        XCTAssertEqual(ClientMemoglyph.lireTravail(statut: 401, corps: nil, erreur: nil, jeton: jeton), .perdu(.jetonRefuse))
    }

    func testProjetsAvecIdentiteSeulement() {
        let corps = json([["id": "p1", "project": "Boîte de réception"], ["id": NSNull(), "project": "à migrer"],
                          ["id": "p2", "project": "Équipe"]])
        XCTAssertEqual(try ClientMemoglyph.lireProjets(statut: 200, corps: corps, erreur: nil, jeton: jeton).get(),
                       [Projet(id: "p1", nom: "Boîte de réception"), Projet(id: "p2", nom: "Équipe")])
        XCTAssertEqual(ClientMemoglyph.lireProjets(statut: 401, corps: nil, erreur: nil, jeton: jeton),
                       .failure(IssueErreur(.jetonRefuse)))
    }

    func testLienVersLaBibliotheque() {
        XCTAssertEqual(ClientMemoglyph.lien(adresse: adresse, projet: "p1").absoluteString,
                       "http://127.0.0.1:8770/?vue=library&projet=p1")
        XCTAssertEqual(ClientMemoglyph.lien(adresse: adresse, projet: nil).absoluteString,
                       "http://127.0.0.1:8770/?vue=library")
    }

    // MARK: - File de renvoi

    func testFileDeRenvoi() {
        var file = FileRenvoi()
        file.ajouter(capture)
        file.ajouter(capture)
        file.ajouter(Capture(fichier: "replay.wav", visio: false, application: "Safari"))
        XCTAssertEqual(file.captures.map(\.fichier), [capture.fichier, "replay.wav"], "pas de doublon")
        let relue = FileRenvoi.lire(file.donnees())
        XCTAssertEqual(relue, file)
        file.retirer(capture.fichier)
        XCTAssertEqual(file.captures.map(\.fichier), ["replay.wav"])
        XCTAssertEqual(FileRenvoi.lire(Data("pas du json".utf8)), FileRenvoi())
        XCTAssertEqual(FileRenvoi.lire(nil), FileRenvoi())
    }

    // MARK: - Replay par PID

    private func p(_ pid: Int32, _ nom: String, _ id: String, joue: Bool = true) -> ProcessusDecrit {
        ProcessusDecrit(objet: UInt32(pid), pid: pid, nom: nom, identifiant: id, joue: joue)
    }

    func testReplayUnCandidatParPIDJamaisToutLeService() {
        let processus = [
            p(41719, "com.apple.WebKit.GPU", "com.apple.WebKit.GPU"),
            p(4261, "com.apple.WebKit.GPU", "com.apple.WebKit.GPU", joue: false),
            p(93824, "com.apple.WebKit.GPU", "com.apple.WebKit.GPU", joue: false),
            p(500, "Google Chrome Helper", "com.google.Chrome.helper"),
        ]
        let c = ChoixReplay.candidats(processus, applications: [("com.google.Chrome", "Google Chrome"), ("com.apple.Safari", "Safari")])
        XCTAssertEqual(c.map(\.processus.pid), [500, 41719], "seulement ceux qui jouent, un par PID")
        XCTAssertEqual(c.map(\.libelle), ["Google Chrome · Google Chrome Helper (PID 500)",
                                          "com.apple.WebKit.GPU (PID 41719)"])
    }

    func testProcessusDisparuEtRemplacants() {
        let suivi = p(41719, "com.apple.WebKit.GPU", "com.apple.WebKit.GPU")
        XCTAssertNil(ChoixReplay.remplacants(de: suivi, parmi: [suivi]), "toujours là")
        let apres = [p(41720, "com.apple.WebKit.GPU", "com.apple.WebKit.GPU"),
                     p(4261, "com.apple.WebKit.GPU", "com.apple.WebKit.GPU", joue: false),
                     p(500, "zoom.us", "us.zoom.xos")]
        XCTAssertEqual(ChoixReplay.remplacants(de: suivi, parmi: apres)?.map(\.pid), [41720])
        let outil = p(77, "afplay", "")
        XCTAssertEqual(ChoixReplay.remplacants(de: outil, parmi: [p(78, "afplay", "")])?.map(\.pid), [78])
        XCTAssertEqual(ChoixReplay.remplacants(de: outil, parmi: [])?.count, 0, "disparu, sans remplaçant")
    }

    // MARK: - Ouvrir la réunion elle-même

    func testLeTravailFiniRendLIdentiteDeLaReunionEtLeLienLOuvre() {
        let corps = json(["status": "done", "result": ["name": "r.md", "meeting_id": "M1", "project_id": "P1"]])
        let etat = ClientMemoglyph.lireTravail(statut: 200, corps: corps, erreur: nil, jeton: jeton)
        XCTAssertEqual(etat, .fini(reunion: "r.md", avertissements: [], meetingId: "M1", projet: "P1"))
        XCTAssertEqual(ClientMemoglyph.lien(adresse: adresse, projet: "P1", reunion: "M1").absoluteString,
                       "http://127.0.0.1:8770/?vue=library&projet=P1&reunion=M1")
        let vide = json(["status": "done", "result": ["name": "r.md", "meeting_id": ""]])
        XCTAssertEqual(ClientMemoglyph.lireTravail(statut: 200, corps: vide, erreur: nil, jeton: jeton),
                       .fini(reunion: "r.md", avertissements: []), "sans identité, la bibliothèque")
    }
}

