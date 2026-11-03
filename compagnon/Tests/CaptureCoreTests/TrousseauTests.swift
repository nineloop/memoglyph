import CaptureCore
import Foundation
import XCTest

/// Le nom de service du trousseau est réglable par l'environnement ; le défaut ne change pas.
/// Pure logique : ce test ne touche JAMAIS au trousseau.
final class TrousseauTests: XCTestCase {
    func testLeDefautNeChangePas() {
        XCTAssertEqual(Espace.serviceParDefaut, "org.memoglyph.compagnon")
        XCTAssertEqual(Espace.service(environnement: [:]), "org.memoglyph.compagnon")
        XCTAssertEqual(Espace.service(environnement: ["AUTRE": "x"]), "org.memoglyph.compagnon")
        XCTAssertEqual(Espace.service(environnement: [Espace.variable: ""]), "org.memoglyph.compagnon")
        XCTAssertEqual(Espace.service(environnement: [Espace.variable: "   "]), "org.memoglyph.compagnon")
    }

    func testLaVariableChoisitUnAutreService() {
        XCTAssertEqual(Espace.service(environnement: [Espace.variable: "org.memoglyph.compagnon.demo"]),
                       "org.memoglyph.compagnon.demo")
        XCTAssertEqual(Espace.service(environnement: [Espace.variable: " essai-9200 "]), "essai-9200")
        XCTAssertEqual(Espace.variable, "MEMOGLYPH_COMPAGNON_SERVICE")
    }

    func testUnNomInvalideRetombeSurLeDefautJamaisSurUneEntreeInattendue() {
        for mauvais in ["a b", "é", "../x", "x/y", "a;b", String(repeating: "a", count: 101), "démo"] {
            XCTAssertEqual(Espace.service(environnement: [Espace.variable: mauvais]), Espace.serviceParDefaut, mauvais)
        }
    }

    func testDossierEtReglagesSuiventLeServicePourIsolerLEssai() {
        let defaut = Espace.serviceParDefaut
        XCTAssertEqual(Espace.suffixe(service: defaut), "")
        XCTAssertEqual(Espace.nomDuDossier(service: defaut), "Captures", "défaut inchangé")
        XCTAssertEqual(Espace.cleDuProjet(service: defaut), "projetEnvoi", "défaut inchangé")
        let demo = "org.memoglyph.compagnon.demo"
        XCTAssertNotEqual(Espace.nomDuDossier(service: demo), "Captures", "la file de renvoi vit dans ce dossier")
        XCTAssertNotEqual(Espace.cleDuProjet(service: demo), "projetEnvoi")
        XCTAssertEqual(Espace.nomDuDossier(service: demo), "Captures-org-memoglyph-compagnon-demo")
        // Deux essais différents ne partagent rien non plus.
        XCTAssertNotEqual(Espace.nomDuDossier(service: "essai-a"), Espace.nomDuDossier(service: "essai-b"))
    }
}
