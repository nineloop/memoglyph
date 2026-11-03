import CaptureCore
import Foundation
import XCTest

/// Le renvoi automatique : calendrier, arrêts immédiats, et idempotence côté client.
final class RenvoiTests: XCTestCase {
    private let jeton = "mgj_" + String(repeating: "Ab1-_", count: 8)
    private func json(_ o: Any) -> Data { try! JSONSerialization.data(withJSONObject: o) }

    func testCalendrierDeHuitRenvois() {
        XCTAssertEqual(Renvoi.delais, [15, 30, 60, 120, 300, 300, 300, 300])
        XCTAssertEqual(Renvoi.renvoisMax, 8)
        let occupe = Issue.refuse(code: 429, raison: "occupé")
        for (i, delai) in Renvoi.delais.enumerated() {
            XCTAssertEqual(Renvoi.decider(occupe, renvoisFaits: i), .reessayer(apres: delai, cause: .occupe), "renvoi \(i + 1)")
        }
    }

    func testApresLeDernierEssaiLaCaptureEstAbandonneeMaisConservee() {
        let occupe = Issue.refuse(code: 429, raison: "occupé")
        XCTAssertEqual(Renvoi.decider(occupe, renvoisFaits: 8), .abandonner(occupe))
        XCTAssertEqual(Renvoi.decider(occupe, renvoisFaits: 99), .abandonner(occupe))
        // « Abandonner » ne retire rien de la file : la capture y reste (voir `FileRenvoi`).
        var file = FileRenvoi()
        let capture = Capture(fichier: "a.wav", visio: true, application: "zoom.us")
        file.ajouter(capture)
        _ = Renvoi.decider(occupe, renvoisFaits: 8)
        XCTAssertEqual(file.captures, [capture])
    }

    func testArretImmediatSur401Et403Et404() {
        for issue in [Issue.jetonRefuse, .introuvable] {
            XCTAssertEqual(Renvoi.decider(issue, renvoisFaits: 0), .abandonner(issue), "pas un seul renvoi")
        }
        // Et par la lecture de la réponse : 401, 403 et 404 donnent bien ces issues.
        for statut in [401, 403] {
            XCTAssertEqual(ClientMemoglyph.lireEnvoi(statut: statut, corps: nil, erreur: nil, jeton: jeton), .jetonRefuse)
        }
        XCTAssertEqual(ClientMemoglyph.lireEnvoi(statut: 404, corps: nil, erreur: nil, jeton: jeton), .introuvable)
    }

    func testTableDeDecision() {
        let cas: [(Issue, DecisionRenvoi)] = [
            (.refuse(code: 429, raison: ""), .reessayer(apres: 15, cause: .occupe)),
            (.injoignable("réseau"), .reessayer(apres: 15, cause: .injoignable)),
            (.refuse(code: 503, raison: ""), .reessayer(apres: 15, cause: .incident)),
            (.refuse(code: 500, raison: ""), .reessayer(apres: 15, cause: .incident)),
            (.refuse(code: 408, raison: ""), .reessayer(apres: 15, cause: .incident)),
            (.refuse(code: 400, raison: "mode inconnu"), .abandonner(.refuse(code: 400, raison: "mode inconnu"))),
            (.refuse(code: 413, raison: "trop gros"), .abandonner(.refuse(code: 413, raison: "trop gros"))),
            (.refuse(code: 422, raison: ""), .abandonner(.refuse(code: 422, raison: ""))),
            (.refuse(code: 0, raison: "lecture de la capture"), .abandonner(.refuse(code: 0, raison: "lecture de la capture"))),
            (.accepte(travail: "j1"), .envoyee),
            (.dejaRecue(reunion: "r.md", travail: nil), .envoyee),
            (.dejaRecue(reunion: nil, travail: "j1"), .envoyee),
        ]
        for (issue, attendu) in cas { XCTAssertEqual(Renvoi.decider(issue, renvoisFaits: 0), attendu, "\(issue)") }
    }

    func testLeCaptureIdResteLeMemeAUtraversDesRenvois() {
        let capture = Capture(fichier: "a.wav", visio: true, application: "zoom.us")
        XCTAssertNotNil(UUID(uuidString: capture.captureId))
        XCTAssertEqual(capture.captureId, capture.captureId.lowercased())
        var vus = Set<String>()
        for renvoi in 0..<Renvoi.renvoisMax {
            // Chaque renvoi reconstruit le corps à partir de la MÊME capture.
            let (entete, _) = ClientMemoglyph.multipart(capture, projet: "p1", frontiere: "F")
            let texte = String(decoding: entete, as: UTF8.self)
            XCTAssertTrue(texte.contains("name=\"capture_id\"\r\n\r\n\(capture.captureId)\r\n"), "renvoi \(renvoi)")
            vus.insert(capture.captureId)
        }
        XCTAssertEqual(vus.count, 1)
        // Après un aller-retour par la file sur disque, le même identifiant.
        var file = FileRenvoi()
        file.ajouter(capture)
        XCTAssertEqual(FileRenvoi.lire(file.donnees()).captures.first?.captureId, capture.captureId)
        // Deux captures n'ont jamais le même.
        XCTAssertNotEqual(capture.captureId, Capture(fichier: "b.wav", visio: true, application: "zoom.us").captureId)
    }

    func testUneReponseDejaRecueVautEnvoyee() {
        let reunion = ClientMemoglyph.lireEnvoi(statut: 200, corps: json(["deja_recue": true, "meeting": "r.md"]),
                                               erreur: nil, jeton: jeton)
        XCTAssertEqual(reunion, .dejaRecue(reunion: "r.md", travail: nil))
        let travail = ClientMemoglyph.lireEnvoi(statut: 200, corps: json(["deja_recue": true, "job_id": "j9"]),
                                               erreur: nil, jeton: jeton)
        XCTAssertEqual(travail, .dejaRecue(reunion: nil, travail: "j9"))
        for issue in [reunion, travail] { XCTAssertEqual(Renvoi.decider(issue, renvoisFaits: 3), .envoyee) }
        // Une réponse « déjà reçue » sans rien à suivre n'est pas crue.
        XCTAssertEqual(ClientMemoglyph.lireEnvoi(statut: 200, corps: json(["deja_recue": true]), erreur: nil, jeton: jeton),
                       .refuse(code: 200, raison: "réponse sans travail"))
    }

    func testUneFileEcriteAvantCeChantierSeRelitEntiere() throws {
        // Le format d'avant : pas de `captureId`. Jeter la file perdrait des captures en attente.
        let ancienne = Data(#"{"captures":[{"fichier":"a.wav","visio":true,"application":"zoom.us"},{"fichier":"b.wav","visio":false,"application":"Safari"}]}"#.utf8)
        let file = FileRenvoi.lire(ancienne)
        XCTAssertEqual(file.captures.map(\.fichier), ["a.wav", "b.wav"])
        let ids = file.captures.map(\.captureId)
        XCTAssertEqual(Set(ids).count, 2)
        ids.forEach { XCTAssertNotNil(UUID(uuidString: $0)) }
        // Réécrite, elle garde les identifiants attribués : le renvoi suivant porte le même.
        XCTAssertEqual(FileRenvoi.lire(file.donnees()).captures.map(\.captureId), ids)
    }

    func testUneFileIllisibleSeDistingueDUneFileVide() {
        XCTAssertNil(FileRenvoi.lireOuNil(Data("pas du json".utf8)), "à mettre de côté, jamais écrasée")
        XCTAssertNil(FileRenvoi.lireOuNil(Data(#"{"captures":[{"fichier":"a.wav"}]}"#.utf8)), "une entrée malformée")
        XCTAssertEqual(FileRenvoi.lireOuNil(Data(#"{"captures":[]}"#.utf8)), FileRenvoi())
        XCTAssertNil(FileRenvoi.lireOuNil(nil), "pas de fichier")
    }
}
