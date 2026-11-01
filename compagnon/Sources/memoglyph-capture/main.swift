import CaptureAudio
import CaptureCore
import Foundation

setvbuf(stdout, nil, _IOLBF, 0)

func erreur(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

func sortir(_ code: CodeSortie) -> Never {
    exit(code.rawValue)
}

func lister(tous: Bool) -> CodeSortie {
    do {
        let processus = try ProcessusAudio.lister().filter { tous || $0.joue }
        guard !processus.isEmpty else {
            print(tous ? "Aucun processus n'a ouvert l'audio." : "Aucun processus ne joue de son en ce moment.")
            return .reussi
        }
        // Un nom plus long que sa colonne est tronqué : l'identifiant reste aligné.
        let largeurs = [8, 34]
        print(Colonnes.ligne(["PID", "NOM", "IDENTIFIANT" + (tous ? "  (joue)" : "")], largeurs: largeurs))
        for p in processus {
            print(Colonnes.ligne([String(p.pid), p.nom,
                                  (p.identifiant.isEmpty ? "—" : p.identifiant) + (tous ? (p.joue ? "  oui" : "  non") : "")],
                                 largeurs: largeurs))
        }
        return .reussi
    } catch {
        erreur("Liste des processus impossible : \(error)")
        return .erreurAudio
    }
}

func enregistrer(cible: Cible, micro: Bool, sortie: String, duree: Double?) -> CodeSortie {
    let vises: [ProcessusDecrit]
    do {
        vises = ProcessusDecrit.choisir(cible, parmi: try ProcessusAudio.lister())
    } catch {
        erreur("Liste des processus impossible : \(error)")
        return .erreurAudio
    }
    guard !vises.isEmpty else {
        erreur("Aucun processus audio ne correspond à « \(cible) ». L'application doit avoir ouvert "
               + "l'audio au moins une fois ; voir « memoglyph-capture applications --tous ».")
        return .cibleIntrouvable
    }
    if micro {
        switch Autorisations.micro(attente: 60) {
        case .accordee:
            break
        case .refusee:
            erreur("Micro refusé par macOS. Réglages Système > Confidentialité et sécurité > Microphone : "
                   + "autorisez l'application qui lance cet outil (le Terminal), ou relancez avec --sans-micro.")
            return .microRefuse
        case .enAttente:
            erreur("Autorisation du micro en attente : répondez à la demande de macOS, puis relancez.")
            return .microRefuse
        }
    }

    let url = URL(fileURLWithPath: sortie)
    let enregistreur = Enregistreur(processus: vises.map(\.objet), micro: micro, sortie: url)
    let fin = DispatchSemaphore(value: 0)
    // C6 : une erreur d'écriture arrête la capture tout de suite (code 6 plus bas).
    enregistreur.surErreur = { _ in fin.signal() }
    do {
        try enregistreur.demarrer()
    } catch {
        erreur("Capture impossible : \(error)")
        return .erreurAudio
    }
    let noms = vises.map { "\($0.nom) (PID \($0.pid))" }.joined(separator: ", ")
    print("● Enregistrement : \(noms)\(micro ? " et le micro" : ", sans micro")")
    print("  Fichier : \(url.path) (\(enregistreur.frequence) Hz, 2 canaux : gauche micro, droite système)")
    if micro { print("  Rappel : les participants d'une visio doivent être prévenus de l'enregistrement.") }
    print("  Ctrl-C pour arrêter\(duree.map { ", arrêt automatique après \($0) s" } ?? "").")

    signal(SIGINT, SIG_IGN)
    let interruption = DispatchSource.makeSignalSource(signal: SIGINT, queue: .global())
    interruption.setEventHandler { fin.signal() }
    interruption.resume()
    if let duree { DispatchQueue.global().asyncAfter(deadline: .now() + duree) { fin.signal() } }
    fin.wait()
    interruption.cancel()

    let bilan: Enregistreur.Bilan
    do {
        bilan = try enregistreur.arreter()
    } catch {
        erreur("Arrêt de la capture en erreur : \(error). Le fichier écrit jusque-là se répare avec "
               + "« memoglyph-capture reparer \(url.path) ».")
        return .erreurAudio
    }
    print("■ Arrêté : \(String(format: "%.1f", bilan.duree)) s, \(bilan.trames) trames ; "
          + "crête système \(String(format: "%.3f", bilan.creteSysteme))"
          + (bilan.creteMicro.map { ", crête micro \(String(format: "%.3f", $0))" } ?? ""))
    // Un canal entièrement nul n'est pas un enregistrement réussi : c'est le seul
    // signe qu'une autorisation manque.
    if bilan.creteSysteme == 0 {
        erreur("ÉCHEC : aucun son reçu de \(noms). Soit l'application n'a rien joué, soit macOS n'autorise "
               + "pas la capture du son des applications (Réglages Système > Confidentialité et sécurité > "
               + "Enregistrement de l'écran et de l'audio du système). Le fichier est gardé, mais il est muet.")
        return .systemeMuet
    }
    if let creteMicro = bilan.creteMicro, creteMicro == 0 {
        erreur("ÉCHEC : le micro n'a rien envoyé. Vérifiez son autorisation et l'entrée par défaut.")
        return .microMuet
    }
    print("✓ Enregistrement réussi : \(url.path)")
    return .reussi
}

let commande: Commande
do {
    commande = try Arguments.analyser(Array(CommandLine.arguments.dropFirst()))
} catch {
    erreur("\(error)\n\n\(Arguments.usage)")
    sortir(.usage)
}

switch commande {
case .aide:
    print(Arguments.usage)
case .applications(let tous):
    sortir(lister(tous: tous))
case .reparer(let chemin):
    do {
        let trames = try EcrivainWAV.reparer(URL(fileURLWithPath: chemin))
        let entete = try EcrivainWAV.lireEntete(URL(fileURLWithPath: chemin))
        print("✓ \(chemin) réparé : \(trames) trames, \(String(format: "%.1f", Double(trames) / Double(entete.frequence))) s.")
    } catch {
        erreur("Réparation impossible : \(error)")
        sortir(.usage)
    }
case .enregistrer(let cible, let micro, let sortie, let duree):
    sortir(enregistrer(cible: cible, micro: micro, sortie: sortie, duree: duree))
}
