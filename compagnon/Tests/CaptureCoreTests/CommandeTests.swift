import CaptureCore
import XCTest

final class CommandeTests: XCTestCase {
    func testSansArgumentOuAvecAideCEstLAide() throws {
        XCTAssertEqual(try Arguments.analyser([]), .aide)
        XCTAssertEqual(try Arguments.analyser(["--aide"]), .aide)
    }

    func testApplications() throws {
        XCTAssertEqual(try Arguments.analyser(["applications"]), .applications(tous: false))
        XCTAssertEqual(try Arguments.analyser(["applications", "--tous"]), .applications(tous: true))
        XCTAssertThrowsError(try Arguments.analyser(["applications", "zoom"]))
    }

    func testEnregistrerParIdentifiantAvecMicroParDefaut() throws {
        XCTAssertEqual(
            try Arguments.analyser(["enregistrer", "--app", "us.zoom.xos", "--sortie", "visio.wav"]),
            .enregistrer(cible: .identifiant("us.zoom.xos"), micro: true, sortie: "visio.wav", duree: nil))
    }

    func testEnregistrerParPIDSansMicroAvecDureeDansNImporteQuelOrdre() throws {
        XCTAssertEqual(
            try Arguments.analyser(["enregistrer", "--duree", "2.5", "--sans-micro",
                                    "--sortie", "/tmp/r.wav", "--app", "4242"]),
            .enregistrer(cible: .pid(4242), micro: false, sortie: "/tmp/r.wav", duree: 2.5))
    }

    func testEnregistrerRefuseUnAppelIncomplet() {
        for arguments in [
            ["enregistrer", "--sortie", "r.wav"],                                    // pas d'application
            ["enregistrer", "--app", "zoom"],                                        // pas de fichier
            ["enregistrer", "--app", "--sortie", "r.wav"],                           // --app sans valeur
            ["enregistrer", "--app", "zoom", "--sortie", "r.wav", "--duree", "0"],
            ["enregistrer", "--app", "zoom", "--sortie", "r.wav", "--duree", "abc"],
            ["enregistrer", "--app", "zoom", "--sortie", "r.wav", "--micro"],        // drapeau inconnu
            ["enregistrer", "--app", "a", "--app", "b", "--sortie", "r.wav"],
        ] {
            XCTAssertThrowsError(try Arguments.analyser(arguments), "\(arguments)")
        }
    }

    func testReparerEtCommandeInconnue() throws {
        XCTAssertEqual(try Arguments.analyser(["reparer", "r.wav"]), .reparer("r.wav"))
        XCTAssertThrowsError(try Arguments.analyser(["reparer"]))
        XCTAssertThrowsError(try Arguments.analyser(["capturer"]))
    }

    func testUnPIDNulOuNegatifEstUnNom() throws {
        XCTAssertEqual(try Arguments.analyser(["enregistrer", "--app", "0", "--sortie", "r.wav"]),
                       .enregistrer(cible: .identifiant("0"), micro: true, sortie: "r.wav", duree: nil))
    }

    // MARK: - Choix des processus

    private let processus = [
        ProcessusDecrit(objet: 1, pid: 10, nom: "zoom.us", identifiant: "us.zoom.xos", joue: true),
        ProcessusDecrit(objet: 2, pid: 20, nom: "Microsoft Teams", identifiant: "com.microsoft.teams2", joue: false),
        ProcessusDecrit(objet: 3, pid: 21, nom: "Microsoft Teams WebView Helper",
                        identifiant: "com.microsoft.teams2.helper", joue: true),
        ProcessusDecrit(objet: 4, pid: 30, nom: "afplay", identifiant: "", joue: true),
        ProcessusDecrit(objet: 5, pid: 40, nom: "Teams", identifiant: "com.microsoft.teams2x", joue: true),
    ]

    func testUnIdentifiantCouvreSesAuxiliairesMaisPasUnVoisin() {
        XCTAssertEqual(ProcessusDecrit.choisir(.identifiant("com.microsoft.teams2"), parmi: processus).map(\.pid),
                       [20, 21])
    }

    func testUnNomDExecutableSuffitPourUnOutilSansPaquet() {
        XCTAssertEqual(ProcessusDecrit.choisir(.identifiant("AFPLAY"), parmi: processus).map(\.pid), [30])
        XCTAssertEqual(ProcessusDecrit.choisir(.pid(10), parmi: processus).map(\.nom), ["zoom.us"])
        XCTAssertEqual(ProcessusDecrit.choisir(.identifiant("skype"), parmi: processus), [])
        XCTAssertEqual(ProcessusDecrit.choisir(.identifiant(""), parmi: processus), [])
    }
}
