import AppKit
import CaptureCore

/// Pont entre la file audio et l'interface : le `Surveillant` tourne sur la file audio, à
/// chaque tampon ; l'interface relève crêtes et alertes à son rythme.
final class Moniteur: @unchecked Sendable {
    private let verrou = NSLock()
    private var surveillant: Surveillant
    private var peripherique: String?
    private var creteMicro: Float = 0
    private var creteSysteme: Float = 0
    private var alertes: [Alerte] = []

    init(peripherique: String?) {
        surveillant = Surveillant(peripherique: peripherique)
        self.peripherique = peripherique
    }

    /// Sur la file audio.
    func recevoir(micro: [Float]?, systeme: [Float], duree: Double) {
        verrou.lock(); defer { verrou.unlock() }
        if let micro { creteMicro = max(creteMicro, Melange.crete(micro)) }
        creteSysteme = max(creteSysteme, Melange.crete(systeme))
        alertes += surveillant.observer(micro: micro, systeme: systeme, duree: duree, peripherique: peripherique)
    }

    func noterPeripherique(_ nom: String?) {
        verrou.lock(); defer { verrou.unlock() }
        peripherique = nom
    }

    /// Crêtes depuis le relevé précédent et alertes nouvelles ; remet le relevé à zéro.
    func relever() -> (micro: Float, systeme: Float, alertes: [Alerte]) {
        verrou.lock(); defer { verrou.unlock() }
        let releve = (creteMicro, creteSysteme, alertes)
        creteMicro = 0; creteSysteme = 0; alertes = []
        return releve
    }
}

/// Pastille flottante, présente seulement pendant l'enregistrement : temps écoulé, niveaux du
/// micro et du système, dernière alerte, bouton Stop. Exclue des captures et du partage
/// d'écran (`sharingType = .none`) : les participants d'une visio ne la voient pas.
@MainActor
final class Pastille: NSObject {
    private let panneau: NSPanel
    private let temps = NSTextField(labelWithString: "00:00")
    private let niveauMicro = Pastille.vumetre()
    private let niveauSysteme = Pastille.vumetre()
    private let ligneMicro: NSStackView
    private let alerte = NSTextField(wrappingLabelWithString: "")
    private let surStop: () -> Void

    init(surStop: @escaping () -> Void) {
        self.surStop = surStop
        panneau = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 280, height: 130),
                          styleMask: [.titled, .nonactivatingPanel, .utilityWindow, .hudWindow],
                          backing: .buffered, defer: true)
        ligneMicro = Pastille.ligne("Micro", niveauMicro)
        super.init()
        panneau.title = "Memoglyph"
        panneau.level = .floating
        panneau.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panneau.hidesOnDeactivate = false   // l'application n'est jamais active : la visio l'est
        panneau.isReleasedWhenClosed = false
        panneau.sharingType = .none

        temps.font = .monospacedDigitSystemFont(ofSize: 20, weight: .medium)
        let stop = NSButton(title: "Stop", target: self, action: #selector(arreter))
        stop.bezelColor = .systemRed
        stop.keyEquivalent = "."
        stop.keyEquivalentModifierMask = .command
        let entete = NSStackView(views: [Pastille.point(), temps, NSView(), stop])
        alerte.textColor = .systemOrange
        alerte.font = .systemFont(ofSize: 11)
        alerte.isHidden = true
        let pile = NSStackView(views: [entete, ligneMicro, Pastille.ligne("Visio", niveauSysteme), alerte])
        pile.orientation = .vertical
        pile.alignment = .leading
        pile.edgeInsets = NSEdgeInsets(top: 8, left: 12, bottom: 10, right: 12)
        for vue in [entete, ligneMicro, alerte] { vue.widthAnchor.constraint(equalTo: pile.widthAnchor, constant: -24).isActive = true }
        panneau.contentView = pile
    }

    func montrer(micro: Bool, cible: String) {
        panneau.title = "Memoglyph · \(cible)"
        ligneMicro.isHidden = !micro
        alerte.stringValue = ""
        alerte.isHidden = true
        afficher(temps: "00:00", micro: 0, systeme: 0)
        if let ecran = NSScreen.main?.visibleFrame {
            panneau.setFrameTopLeftPoint(NSPoint(x: ecran.maxX - panneau.frame.width - 16, y: ecran.maxY - 16))
        }
        panneau.orderFrontRegardless()
    }

    func cacher() { panneau.orderOut(nil) }

    func afficher(temps texte: String, micro: Float, systeme: Float) {
        temps.stringValue = texte
        niveauMicro.doubleValue = Niveau.affiche(crete: micro)
        niveauSysteme.doubleValue = Niveau.affiche(crete: systeme)
    }

    /// La dernière alerte reste affichée jusqu'à la suivante ou jusqu'au Stop.
    func signaler(_ nouvelle: Alerte) {
        alerte.stringValue = "⚠︎ " + nouvelle.texte
        alerte.isHidden = false
    }

    @objc private func arreter() { surStop() }

    private static func vumetre() -> NSLevelIndicator {
        let v = NSLevelIndicator()
        v.levelIndicatorStyle = .continuousCapacity
        v.minValue = 0
        v.maxValue = 1
        v.warningValue = 0.8
        v.criticalValue = 0.97
        v.widthAnchor.constraint(equalToConstant: 200).isActive = true
        return v
    }

    private static func ligne(_ nom: String, _ vumetre: NSLevelIndicator) -> NSStackView {
        let etiquette = NSTextField(labelWithString: nom)
        etiquette.font = .systemFont(ofSize: 11)
        etiquette.widthAnchor.constraint(equalToConstant: 40).isActive = true
        return NSStackView(views: [etiquette, vumetre])
    }

    private static func point() -> NSImageView {
        let image = NSImageView(image: NSImage(systemSymbolName: "record.circle.fill",
                                               accessibilityDescription: "Enregistrement") ?? NSImage())
        image.contentTintColor = .systemRed
        return image
    }
}
