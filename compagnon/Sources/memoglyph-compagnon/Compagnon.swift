import AppKit
import CaptureAudio
import CaptureCore

/// L'icône de la barre de menus : démarrer en Visio (micro et son de l'application) ou en
/// Replay (son d'un processus seul), arrêter, envoyer à Memoglyph, retrouver les captures.
/// Rien ne démarre sans un choix explicite, et chaque démarrage passe par les rappels de
/// `Rappels` et affiche la destination.
@MainActor
final class Compagnon: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var icone: NSStatusItem!
    private lazy var pastille = Pastille { [weak self] in self?.arreter() }
    private var etat = EtatPastille.cachee
    private var enregistreur: Enregistreur?
    private var moniteur: Moniteur?
    private var minuterie: Timer?
    private var enCours: (cible: String, visio: Bool, fichier: URL, suivi: ProcessusDecrit?)?
    private var relevesDepuisPeripherique = 0
    private var remplacants: [ProcessusDecrit] = []
    private var disparuSignale = false

    private var liaison = Trousseau.lire()
    private var joignable: Bool?
    private var projets: [Projet] = []
    private var file = FileRenvoi()
    /// Où en est chaque envoi, par fichier ; la dernière réunion prête, pour son lien.
    private var etatsEnvoi: [String: String] = [:]
    private var prete: (reunion: String, projet: String?, meetingId: String?)?

    /// Les captures restent sur ce Mac, envoyées ou non : Memoglyph en reçoit une copie.
    static let dossier = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Memoglyph/Captures", isDirectory: true)
    private static let fichierFile = dossier.appendingPathComponent(".envoi-en-attente.json")
    private static let cleProjet = "projetEnvoi"

    private var projetChoisi: String? {
        get { UserDefaults.standard.string(forKey: Compagnon.cleProjet) }
        set { UserDefaults.standard.set(newValue, forKey: Compagnon.cleProjet) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        icone = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        menu.delegate = self
        icone.menu = menu
        file = FileRenvoi.lire(try? Data(contentsOf: Compagnon.fichierFile))
        dessinerIcone()
        rafraichir()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if enregistreur != nil { arreter() }
        return .terminateNow
    }

    // MARK: - Menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        rafraichir()
        menu.removeAllItems()
        menu.addItem(NSMenuItem(title: destination(), action: nil, keyEquivalent: ""))
        menu.addItem(.separator())
        if let enCours, etat.visible {
            let titre = "● \(enCours.visio ? "Visio" : "Replay") · \(enCours.cible) · \(etat.temps(maintenant: maintenant()))"
            menu.addItem(NSMenuItem(title: titre, action: nil, keyEquivalent: ""))
            for p in remplacants {
                let item = element("Reprendre sur \(p.nom) (PID \(p.pid))", #selector(reprendre))
                item.representedObject = p.pid
                menu.addItem(item)
            }
            menu.addItem(element("Arrêter", #selector(arreterDepuisMenu), touche: "."))
        } else {
            menu.addItem(sousMenuVisio())
            menu.addItem(sousMenuReplay())
        }
        menu.addItem(.separator())
        for (fichier, texte) in etatsEnvoi.sorted(by: { $0.key < $1.key }) {
            menu.addItem(NSMenuItem(title: "\(fichier) : \(texte)", action: nil, keyEquivalent: ""))
        }
        if let prete {
            menu.addItem(element("Ouvrir « \(prete.reunion) » dans Memoglyph", #selector(ouvrirReunion)))
        }
        if !file.captures.isEmpty {
            menu.addItem(element("Renvoyer les captures en attente (\(file.captures.count))", #selector(renvoyer)))
        }
        menu.addItem(sousMenuProjet())
        menu.addItem(element(liaison == nil ? "Relier à Memoglyph…" : "Changer la liaison à Memoglyph…", #selector(relier)))
        menu.addItem(element("Ouvrir le dossier des captures", #selector(ouvrirDossier)))
        menu.addItem(element("Quitter", #selector(quitter), touche: "q"))
    }

    private func element(_ titre: String, _ action: Selector, touche: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: titre, action: action, keyEquivalent: touche)
        item.target = self
        return item
    }

    private func destination() -> String {
        guard let liaison else { return "○ Memoglyph — non relié" }
        let hote = liaison.adresse.host ?? ""
        let lieu = ["127.0.0.1", "localhost", "::1"].contains(hote) ? "ce Mac" : hote
        let projet = projets.first { $0.id == projetChoisi }?.nom ?? "projet par défaut"
        switch joignable {
        case true?: return "● Memoglyph — \(lieu) · \(projet)"
        case false?: return "○ Memoglyph — \(lieu), injoignable"
        case nil: return "◌ Memoglyph — \(lieu)"
        }
    }

    /// Visio : les appels en cours en tête, puis une ligne par application ouverte qui a
    /// l'audio ouvert, puis les autres processus et tout le son du Mac. Le choix est calculé
    /// par `ChoixVisio` ; chaque ligne vise l'identifiant du parent, qui couvre ses auxiliaires.
    private func sousMenuVisio() -> NSMenuItem {
        let item = NSMenuItem(title: "Démarrer en Visio", action: nil, keyEquivalent: "")
        let sous = NSMenu()
        do {
            let applications = NSWorkspace.shared.runningApplications
                .filter { $0.activationPolicy == .regular }
                .compactMap { a in a.bundleIdentifier.map { ($0, a.localizedName ?? $0) } }
            let menu = ChoixVisio.menu(try ProcessusAudio.lister(), applications: applications)
            if menu.appels.isEmpty {
                // Sans action, l'élément est grisé.
                sous.addItem(NSMenuItem(title: ChoixVisio.aucunAppel, action: nil, keyEquivalent: ""))
            }
            for appel in menu.appels {
                let ligne = ligneVisio(appel)
                ligne.attributedTitle = NSAttributedString(
                    string: appel.libelle, attributes: [.font: NSFont.boldSystemFont(ofSize: NSFont.systemFontSize)])
                ligne.image = NSImage(systemSymbolName: "phone.fill", accessibilityDescription: "Appel en cours")
                sous.addItem(ligne)
            }
            sous.addItem(.separator())
            menu.applications.forEach { sous.addItem(ligneVisio($0)) }
            if !menu.autres.isEmpty {
                let autres = NSMenuItem(title: "Autres processus…", action: nil, keyEquivalent: "")
                let liste = NSMenu()
                for p in menu.autres {
                    let ligne = ligneVisio(p)
                    if p.cible != p.nom { ligne.title += "  (\(p.cible))" }
                    liste.addItem(ligne)
                }
                autres.submenu = liste
                sous.addItem(autres)
            }
            sous.addItem(.separator())
            sous.addItem(element(ChoixVisio.toutLeSon, #selector(demarrerToutLeSon)))
        } catch {
            sous.addItem(NSMenuItem(title: "Liste impossible : \(error)", action: nil, keyEquivalent: ""))
        }
        item.submenu = sous
        return item
    }

    private func ligneVisio(_ l: LigneVisio) -> NSMenuItem {
        let ligne = element(l.libelle, #selector(demarrerVisio))
        ligne.representedObject = [l.cible, l.nom]
        return ligne
    }

    /// Replay : les processus qui jouent en ce moment, un par PID. Un service partagé comme
    /// `com.apple.WebKit.GPU` sert plusieurs applications : n'en prendre qu'un.
    private func sousMenuReplay() -> NSMenuItem {
        let item = NSMenuItem(title: "Démarrer en Replay", action: nil, keyEquivalent: "")
        let sous = NSMenu()
        do {
            let applications = NSWorkspace.shared.runningApplications.compactMap { a in
                a.bundleIdentifier.map { ($0, a.localizedName ?? $0) }
            }
            let candidats = ChoixReplay.candidats(try ProcessusAudio.lister(), applications: applications)
            for c in candidats {
                let ligne = element(c.libelle, #selector(demarrerReplay))
                ligne.representedObject = c.processus.pid
                sous.addItem(ligne)
            }
            if candidats.isEmpty {
                sous.addItem(NSMenuItem(title: "Rien ne joue : lancez la vidéo, puis rouvrez ce menu", action: nil,
                                        keyEquivalent: ""))
            }
        } catch {
            sous.addItem(NSMenuItem(title: "Liste impossible : \(error)", action: nil, keyEquivalent: ""))
        }
        item.submenu = sous
        return item
    }

    /// Le projet d'envoi : ceux du compte ; le compagnon n'en crée jamais.
    private func sousMenuProjet() -> NSMenuItem {
        let item = NSMenuItem(title: "Projet d'envoi", action: nil, keyEquivalent: "")
        let sous = NSMenu()
        let defaut = element("Projet par défaut du compte", #selector(choisirProjet))
        defaut.state = projetChoisi == nil ? .on : .off
        sous.addItem(defaut)
        for p in projets {
            let ligne = element(p.nom, #selector(choisirProjet))
            ligne.representedObject = p.id
            ligne.state = projetChoisi == p.id ? .on : .off
            sous.addItem(ligne)
        }
        item.submenu = sous
        item.isEnabled = liaison != nil
        return item
    }

    @objc private func demarrerVisio(_ item: NSMenuItem) {
        guard let choix = item.representedObject as? [String], choix.count == 2 else { return }
        demander(nom: choix[1], visio: true) { [weak self] in
            self?.demarrer(nom: choix[1], visio: true, suivi: nil) {
                ProcessusDecrit.choisir(.identifiant(choix[0]), parmi: $0)
            }
        }
    }

    /// Tout le son du Mac et le micro : un tap global qui exclut le compagnon lui-même.
    @objc private func demarrerToutLeSon() {
        let nom = ChoixVisio.toutLeSon
        demander(nom: nom, visio: true) { [weak self] in
            self?.demarrer(nom: nom, visio: true, suivi: nil, global: true) {
                ChoixVisio.exclusDuTout($0, pid: getpid())
            }
        }
    }

    @objc private func demarrerReplay(_ item: NSMenuItem) {
        guard let pid = item.representedObject as? Int32 else { return }
        let nom = item.title
        demander(nom: nom, visio: false) { [weak self] in self?.lancerReplay(pid: pid, nom: nom) }
    }

    private func lancerReplay(pid: Int32, nom: String) {
        let suivi = (try? ProcessusAudio.lister())?.first { $0.pid == pid }
        demarrer(nom: nom, visio: false, suivi: suivi) { $0.filter { $0.pid == pid } }
    }

    /// Le processus suivi a disparu : arrêter (la capture part comme les autres), puis
    /// reprendre sur celui que l'utilisateur a choisi, sans redemander la confirmation.
    @objc private func reprendre(_ item: NSMenuItem) {
        guard let pid = item.representedObject as? Int32, let p = remplacants.first(where: { $0.pid == pid }) else { return }
        arreter(bilanModal: false)
        lancerReplay(pid: p.pid, nom: "\(p.nom) (PID \(p.pid))")
    }

    @objc private func arreterDepuisMenu() { arreter() }
    @objc private func choisirProjet(_ item: NSMenuItem) { projetChoisi = item.representedObject as? String }
    @objc private func ouvrirDossier() {
        try? FileManager.default.createDirectory(at: Compagnon.dossier, withIntermediateDirectories: true)
        NSWorkspace.shared.open(Compagnon.dossier)
    }
    @objc private func ouvrirReunion() {
        guard let liaison, let prete else { return }
        NSWorkspace.shared.open(ClientMemoglyph.lien(adresse: liaison.adresse, projet: prete.projet,
                                                     reunion: prete.meetingId))
    }
    @objc private func quitter() { NSApp.terminate(nil) }

    // MARK: - Démarrer

    private func demander(nom: String, visio: Bool, puis: @escaping () -> Void) {
        let rappels = Rappels.auDemarrage(visio: visio) + ["Destination : \(destination().dropFirst(2))"]
        guard confirmer("Enregistrer \(nom) en \(visio ? "Visio" : "Replay") ?",
                        rappels.map { "• " + $0 }.joined(separator: "\n"), "Démarrer") else { return }
        guard visio else { return puis() }
        // La demande d'autorisation peut attendre une réponse : hors de la file principale.
        DispatchQueue.global().async {
            let autorisation = Autorisations.micro(attente: 60)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    switch autorisation {
                    case .accordee:
                        puis()
                    case .refusee:
                        self.informer("Micro refusé par macOS",
                                      "Réglages Système > Confidentialité et sécurité > Microphone : autorisez "
                                      + "« Memoglyph compagnon » (ou, lancé hors de son paquet .app, l'application "
                                      + "qui l'a lancé, comme le Terminal), ou démarrez en Replay (sans micro).")
                    case .enAttente:
                        self.informer("Autorisation du micro en attente",
                                      "Répondez à la demande de macOS, puis démarrez à nouveau.")
                    }
                }
            }
        }
    }

    /// `viser` rend les processus captés ; en `global`, ceux que le tap de tout le son exclut.
    private func demarrer(nom: String, visio: Bool, suivi: ProcessusDecrit?, global: Bool = false,
                          viser: ([ProcessusDecrit]) -> [ProcessusDecrit]) {
        let vises: [ProcessusDecrit]
        do {
            vises = viser(try ProcessusAudio.lister())
            try FileManager.default.createDirectory(at: Compagnon.dossier, withIntermediateDirectories: true)
        } catch {
            return informer("Capture impossible", "\(error)")
        }
        guard global || !vises.isEmpty else {
            return informer("\(nom) n'a plus d'audio ouvert", "Relancez le son de l'application, puis démarrez à nouveau.")
        }
        let fichier = Compagnon.dossier.appendingPathComponent(NomCapture.fichier(visio: visio, debut: Date()))
        let moniteur = Moniteur(peripherique: visio ? Enregistreur.microParDefaut() : nil)
        let enregistreur = Enregistreur(processus: vises.map(\.objet), micro: visio, sortie: fichier, global: global)
        enregistreur.surTampon = { micro, systeme, duree in moniteur.recevoir(micro: micro, systeme: systeme, duree: duree) }
        // C6 : une erreur d'écriture arrête la capture tout de suite ; `arreter` la montre.
        enregistreur.surErreur = { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.arreter() } }
        }
        do {
            try enregistreur.demarrer()
        } catch {
            return informer("Capture impossible", "\(error)")
        }
        self.enregistreur = enregistreur
        self.moniteur = moniteur
        enCours = (nom, visio, fichier, suivi)
        remplacants = []
        disparuSignale = false
        etat = .enregistrement(debut: maintenant())
        relevesDepuisPeripherique = 0
        pastille.montrer(micro: visio, cible: nom)
        dessinerIcone()
        minuterie = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.relever() }
        }
    }

    /// Dix fois par seconde : niveaux et alertes. Une fois par seconde : le micro par défaut
    /// en Visio, le processus suivi en Replay.
    private func relever() {
        guard let moniteur, let enCours else { return }
        let releve = moniteur.relever()
        pastille.afficher(temps: etat.temps(maintenant: maintenant()), micro: releve.micro, systeme: releve.systeme)
        releve.alertes.forEach(pastille.signaler)
        relevesDepuisPeripherique += 1
        guard relevesDepuisPeripherique >= 10 else { return }
        relevesDepuisPeripherique = 0
        if enCours.visio {
            moniteur.noterPeripherique(Enregistreur.microParDefaut())
        } else if let suivi = enCours.suivi, let liste = try? ProcessusAudio.lister(),
                  let nouveaux = ChoixReplay.remplacants(de: suivi, parmi: liste) {
            // Une alerte à la disparition ; les remplaçants, eux, se mettent à jour dans le menu.
            if !disparuSignale { pastille.signaler(.processusDisparu) }
            disparuSignale = true
            remplacants = nouveaux
        }
    }

    // MARK: - Arrêter

    private func arreter(bilanModal: Bool = true) {
        guard let enregistreur, let enCours else { return }
        minuterie?.invalidate()
        minuterie = nil
        pastille.cacher()
        etat = .cachee
        self.enregistreur = nil
        moniteur = nil
        self.enCours = nil
        remplacants = []
        dessinerIcone()
        let bilan: Enregistreur.Bilan
        do {
            bilan = try enregistreur.arreter()
        } catch {
            return informer("Arrêt de la capture en erreur",
                            "\(error)\n\nLe fichier écrit jusque-là se répare avec « memoglyph-capture reparer "
                            + "\(enCours.fichier.path) ».", fichier: enCours.fichier)
        }
        let duree = String(format: "%.0f s", bilan.duree)
        // Un canal entièrement nul n'est pas un enregistrement réussi (voir memoglyph-capture) :
        // il n'est pas envoyé.
        if bilan.creteSysteme == 0 {
            return informer("Aucun son reçu de \(enCours.cible)",
                            "Soit l'application n'a rien joué, soit macOS n'autorise pas la capture du son des "
                            + "applications (Réglages Système > Confidentialité et sécurité > Enregistrement de "
                            + "l'écran et de l'audio du système). Le fichier (\(duree)) est gardé, mais il est muet.",
                            fichier: enCours.fichier)
        }
        if let creteMicro = bilan.creteMicro, creteMicro == 0 {
            return informer("Le micro n'a rien envoyé",
                            "Vérifiez son autorisation et l'entrée par défaut. Le fichier (\(duree)) n'a que le son "
                            + "de la visio ; il n'est pas envoyé.", fichier: enCours.fichier)
        }
        let capture = Capture(fichier: enCours.fichier.lastPathComponent, visio: enCours.visio,
                              application: enCours.suivi?.nom ?? enCours.cible)
        mettreEnFile(capture)
        let suite = liaison == nil ? "Reliez le compagnon à Memoglyph pour l'envoyer ; elle attend dans le menu."
                                   : "Envoi à Memoglyph en cours : son état est dans le menu."
        if bilanModal {
            informer("Enregistrement réussi (\(duree))", enCours.fichier.path + "\n\n" + suite, fichier: enCours.fichier)
        }
        envoyer(capture)
    }

    // MARK: - Memoglyph

    /// Santé de Memoglyph et projets du compte, à l'ouverture du menu ; servis au suivant.
    private func rafraichir() {
        guard let liaison else { joignable = nil; projets = []; return }
        Liaison.sante(liaison.adresse) { ok in MainActor.assumeIsolated { self.joignable = ok } }
        Liaison.projets(liaison.adresse, jeton: liaison.jeton) { resultat in
            MainActor.assumeIsolated { if case .success(let liste) = resultat { self.projets = liste } }
        }
    }

    @objc private func relier() {
        let adresse = NSTextField(string: liaison?.adresse.absoluteString ?? "http://127.0.0.1:8770")
        let jeton = NSSecureTextField(string: "")
        jeton.placeholderString = "mgj_…"
        for champ in [adresse, jeton] { champ.widthAnchor.constraint(equalToConstant: 300).isActive = true }
        let pile = NSStackView(views: [NSTextField(labelWithString: "Adresse de l'interface"), adresse,
                                       NSTextField(labelWithString: "Jeton d'appareil"), jeton])
        pile.orientation = .vertical
        pile.alignment = .leading
        pile.frame = NSRect(x: 0, y: 0, width: 300, height: 110)
        let alerte = NSAlert()
        alerte.messageText = "Relier à Memoglyph"
        alerte.informativeText = "Créez un jeton dans Memoglyph, « Appareils », puis collez-le ici. Il est rangé "
            + "dans le trousseau de macOS, nulle part ailleurs."
        alerte.accessoryView = pile
        alerte.addButton(withTitle: "Relier")
        alerte.addButton(withTitle: "Annuler")
        if liaison != nil { alerte.addButton(withTitle: "Oublier la liaison") }
        NSApp.activate(ignoringOtherApps: true)
        alerte.window.initialFirstResponder = jeton
        switch alerte.runModal() {
        case .alertFirstButtonReturn: break
        case .alertThirdButtonReturn:
            Trousseau.oublier()
            liaison = nil
            return
        default: return
        }
        guard let url = ClientMemoglyph.adresse(adresse.stringValue) else {
            return informer("Adresse invalide", "Attendu : http://127.0.0.1:8770, sans chemin.")
        }
        let secret = jeton.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ClientMemoglyph.jetonValide(secret) else {
            return informer("Jeton invalide", "Un jeton d'appareil commence par mgj_ ; copiez-le en entier depuis « Appareils ».")
        }
        // Le jeton n'est rangé qu'après avoir ouvert la liste des projets du compte.
        Liaison.projets(url, jeton: secret) { resultat in
            MainActor.assumeIsolated {
                switch resultat {
                case .failure(let e):
                    self.informer("Liaison refusée", e.issue.texte)
                case .success(let liste):
                    let statut = Trousseau.ecrire(adresse: url, jeton: secret)
                    guard statut == errSecSuccess else {
                        return self.informer("Le trousseau refuse le jeton", Trousseau.message(statut))
                    }
                    self.liaison = (url, secret)
                    self.projets = liste
                    self.joignable = true
                    if let choisi = self.projetChoisi, !liste.contains(where: { $0.id == choisi }) { self.projetChoisi = nil }
                    if !self.file.captures.isEmpty { self.renvoyer() }
                }
            }
        }
    }

    @objc private func renvoyer() { file.captures.forEach(envoyer) }

    private func mettreEnFile(_ capture: Capture) {
        file.ajouter(capture)
        enregistrerFile()
    }

    private func enregistrerFile() {
        try? file.donnees().write(to: Compagnon.fichierFile, options: .atomic)
    }

    /// Envoie une capture et suit son travail. Elle ne quitte la file qu'une fois reçue.
    private func envoyer(_ capture: Capture) {
        guard let liaison else { etatsEnvoi[capture.fichier] = "en attente d'une liaison"; return }
        guard etatsEnvoi[capture.fichier]?.hasPrefix("envoi") != true else { return }
        etatsEnvoi[capture.fichier] = "envoi…"
        let projet = projetChoisi
        Liaison.envoyer(capture, dossier: Compagnon.dossier, projet: projet,
                        adresse: liaison.adresse, jeton: liaison.jeton) { issue in
            MainActor.assumeIsolated {
                switch issue {
                case .accepte(let travail):
                    self.file.retirer(capture.fichier)
                    self.enregistrerFile()
                    self.etatsEnvoi[capture.fichier] = "reçue, traitement…"
                    self.suivre(travail, fichier: capture.fichier, projet: projet)
                default:
                    self.etatsEnvoi[capture.fichier] = "non envoyée, \(issue.texte)"
                }
            }
        }
    }

    private func suivre(_ travail: String, fichier: String, projet: String?) {
        guard let liaison else { return }
        Liaison.travail(travail, adresse: liaison.adresse, jeton: liaison.jeton) { etat in
            MainActor.assumeIsolated {
                switch etat {
                case .enCours(let progression, let message):
                    self.etatsEnvoi[fichier] = "\(Int(progression * 100)) % \(message)"
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                        MainActor.assumeIsolated { self.suivre(travail, fichier: fichier, projet: projet) }
                    }
                case .fini(let reunion, let avertissements, let meetingId, let projetReunion):
                    self.etatsEnvoi[fichier] = "prête" + (avertissements.isEmpty ? "" : " (\(avertissements.count) avertissement(s))")
                    self.prete = (reunion, projetReunion ?? projet, meetingId)
                case .echec(let raison):
                    self.etatsEnvoi[fichier] = "échec du traitement : \(raison)"
                case .perdu(let issue):
                    self.etatsEnvoi[fichier] = "suivi interrompu : \(issue.texte)"
                }
            }
        }
    }

    // MARK: - Outils

    private func maintenant() -> Double { Date().timeIntervalSinceReferenceDate }

    private func dessinerIcone() {
        let actif = enregistreur != nil
        icone.button?.image = NSImage(systemSymbolName: actif ? "record.circle.fill" : "waveform.circle",
                                      accessibilityDescription: actif ? "Memoglyph : enregistrement" : "Memoglyph")
        icone.button?.contentTintColor = actif ? .systemRed : nil
    }

    private func confirmer(_ titre: String, _ texte: String, _ bouton: String) -> Bool {
        let alerte = NSAlert()
        alerte.messageText = titre
        alerte.informativeText = texte
        alerte.addButton(withTitle: bouton)
        alerte.addButton(withTitle: "Annuler")
        NSApp.activate(ignoringOtherApps: true)
        return alerte.runModal() == .alertFirstButtonReturn
    }

    private func informer(_ titre: String, _ texte: String, fichier: URL? = nil) {
        let alerte = NSAlert()
        alerte.messageText = titre
        alerte.informativeText = texte
        alerte.addButton(withTitle: "OK")
        if fichier != nil { alerte.addButton(withTitle: "Afficher dans le Finder") }
        NSApp.activate(ignoringOtherApps: true)
        if alerte.runModal() == .alertSecondButtonReturn, let fichier {
            NSWorkspace.shared.activateFileViewerSelecting([fichier])
        }
    }
}
