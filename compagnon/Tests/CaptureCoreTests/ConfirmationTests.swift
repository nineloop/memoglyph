import CaptureCore
import Foundation
import XCTest

/// Confirmation au lancement, lien « Suivre », et ce que le jeton d'appareil laisse voir des traitements.
final class ConfirmationTests: XCTestCase {
    private let jeton = "mgj_" + String(repeating: "Ab1-_", count: 8)
    private let adresse = URL(string: "http://127.0.0.1:9200")!
    private func json(_ o: Any) -> Data { try! JSONSerialization.data(withJSONObject: o) }

    func testLeLienSuivreOuvreLaVueDesTraitements() {
        XCTAssertEqual(ClientMemoglyph.lienSuivre(adresse: adresse).absoluteString, "http://127.0.0.1:9200/?traitements=1")
        XCTAssertEqual(ClientMemoglyph.lienSuivre(adresse: URL(string: "https://memoglyph.exemple")!).absoluteString,
                       "https://memoglyph.exemple/?traitements=1")
    }

    func testConfirmationRangeeDansLeProjetChoisi() {
        XCTAssertEqual(Texte.confirmation(projet: "Équipe", autresEnCours: 0, .fr), "Rangé dans Équipe.")
        XCTAssertTrue(Texte.confirmation(projet: "Équipe", autresEnCours: 2, .fr).hasSuffix("En attente : un traitement est déjà en cours."))
    }

    func testLaListeDesTraitementsEstUneRouteDuJetonEnLectureSeule() {
        let q = ClientMemoglyph.requete(.traitements, adresse: adresse, jeton: jeton)
        XCTAssertEqual("\(q.httpMethod!) \(q.url!.path)", "GET /api/jobs")
        XCTAssertEqual(q.value(forHTTPHeaderField: "Authorization"), "Bearer \(jeton)")
        XCTAssertFalse(q.url!.absoluteString.contains(jeton))
    }

    func testSeulsLesComptesEtLesPropresTraitementsSontLus() {
        // Ce que le serveur renvoie avec un jeton d'appareil : les comptes et SES captures. Le client ne
        // lit de plus que id, titre, projet et statut : rien d'autre ne peut finir dans le menu.
        let corps = json([
            "actifs": 2, "en_attente": 1,
            "traitements": [["id": "j1", "titre": "visio-lundi", "projet_nom": "Équipe", "statut": "running",
                             "message_code": "step.report.write", "debut": 1.0, "meeting": ["name": "x.md"]]],
        ])
        let c = try! ClientMemoglyph.lireComptes(statut: 200, corps: corps, erreur: nil, jeton: jeton).get()
        XCTAssertEqual(c.actifs, 2)
        XCTAssertEqual(c.enAttente, 1)
        XCTAssertEqual(c.captures, [TraitementSuivi(id: "j1", titre: "visio-lundi", projet: "Équipe", statut: "running")])
        // Sans traitement à soi : le nombre seul, aucun titre.
        let autres = try! ClientMemoglyph.lireComptes(statut: 200, corps: json(["actifs": 3, "en_attente": 0, "traitements": []]),
                                                      erreur: nil, jeton: jeton).get()
        XCTAssertEqual(autres, Comptes(actifs: 3, enAttente: 0, captures: []))
        XCTAssertEqual(Texte.enAttente(actifs: autres.actifs, .fr), "en attente : 3 traitements en cours")
    }

    func testLeJetonNApparaitPasDansLaListe() {
        let piege = "titre \(jeton)"
        let corps = json(["actifs": 0, "en_attente": 0,
                          "traitements": [["id": "j1", "titre": piege, "projet_nom": piege, "statut": "done"]]])
        let c = try! ClientMemoglyph.lireComptes(statut: 200, corps: corps, erreur: nil, jeton: jeton).get()
        XCTAssertFalse(c.captures[0].titre.contains(jeton))
        XCTAssertFalse(c.captures[0].projet.contains(jeton))
    }

    func testListeRefuseeOuIntrouvable() {
        XCTAssertEqual(ClientMemoglyph.lireComptes(statut: 401, corps: nil, erreur: nil, jeton: jeton),
                       .failure(IssueErreur(.jetonRefuse)))
        XCTAssertEqual(ClientMemoglyph.lireComptes(statut: nil, corps: nil, erreur: "réseau", jeton: jeton),
                       .failure(IssueErreur(.injoignable("réseau"))))
    }

    func testLeTravailEnCoursPorteLeCodeEtLAttente() {
        let corps = json(["status": "running", "progress": 0.6, "message": "Writing the report…",
                          "message_code": "step.report.write", "en_attente": false])
        XCTAssertEqual(ClientMemoglyph.lireTravail(statut: 200, corps: corps, erreur: nil, jeton: jeton),
                       .enCours(progression: 0.6, message: "Writing the report…", code: "step.report.write", enAttente: false))
        let attente = json(["status": "pending", "progress": 0, "message": ""])
        XCTAssertEqual(ClientMemoglyph.lireTravail(statut: 200, corps: attente, erreur: nil, jeton: jeton),
                       .enCours(progression: 0, message: "", code: nil, enAttente: true))
        let creneau = json(["status": "running", "progress": 0, "message": "", "en_attente": true])
        XCTAssertEqual(ClientMemoglyph.lireTravail(statut: 200, corps: creneau, erreur: nil, jeton: jeton),
                       .enCours(progression: 0, message: "", code: nil, enAttente: true))
    }
}
