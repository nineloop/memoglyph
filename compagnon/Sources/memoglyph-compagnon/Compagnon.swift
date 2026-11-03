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
    /// Le jeton est-il refusé ? Posé par tout appel qui rend 401 ou 403, levé par un appel réussi ou une nouvelle liaison.
    private var liaisonRefusee = false
    private var projets: [Projet] = []
    private var file = FileRenvoi()
    private let langue = Langue.systeme
    /// Où en est chaque capture, par fichier (l'état, pas la phrase : la phrase se traduit à l'affichage).
    private var etats: [String: EtatCapture] = [:]
    /// Renvois automatiques déjà faits, par fichier ; remis à zéro par un renvoi manuel.
    private var renvois: [String: Int] = [:]
    private var minuteries: [String: DispatchWorkItem] = [:]
    /// La réunion de chaque capture prête, pour ouvrir cette réunion-là.
    private var pretes: [String: (reunion: String, projet: String?, meetingId: String?)] = [:]
    /// Ce que le serveur dit des traitements (le nombre, et SES captures seulement), relevé à l'ouverture du menu.
    private var comptes: Comptes?
    /// La dernière confirmation (« Rangé dans … »), avec « Suivre ».
    private var confirmation: String?
    /// La capture à laquelle se rapporte la confirmation, et son projet : l'attente se met à jour avec son suivi.
    private var confirmationDe: (fichier: String, projet: String?)?

    /// Les captures restent sur ce Mac, envoyées ou non : Memoglyph en reçoit une copie. Pour un essai
    /// (`MEMOGLYPH_COMPAGNON_SERVICE`), le dossier — donc la file de renvoi — et le réglage du projet
    /// sont à part : l'essai ne touche pas aux captures du compagnon habituel.
    static let dossier = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Memoglyph/\(Espace.nomDuDossier(service: Trousseau.service))", isDirectory: true)
    private static let fichierFile = dossier.appendingPathComponent(".envoi-en-attente.json")
    private static let cleProjet = Espace.cleDuProjet(service: Trousseau.service)

    private var projetChoisi: String? {
        get { UserDefaults.standard.string(forKey: Compagnon.cleProjet) }
        set { UserDefaults.standard.set(newValue, forKey: Compagnon.cleProjet) }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        icone = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        menu.delegate = self
        icone.menu = menu
        let donnees = try? Data(contentsOf: Compagnon.fichierFile)
        if let lue = FileRenvoi.lireOuNil(donnees) {
            file = lue
            enregistrerFile()   // une file d'avant ce chantier reçoit ses identifiants de capture, une fois pour toutes
        } else if donnees != nil {
            // Illisible : mise de côté, jamais écrasée par une file vide.
            let ecart = Compagnon.fichierFile.deletingLastPathComponent().appendingPathComponent(".envoi-en-attente.illisible.json")
            try? FileManager.default.moveItem(at: Compagnon.fichierFile, to: ecart)
        }
        dessinerIcone()
        rafraichir()
        // Une capture restée dans la file (Memoglyph arrêté, Mac éteint) repart seule, sans geste.
        renvoyer()
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if enregistreur != nil { arreter() }
        return .terminateNow
    }

    // MARK: - Menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        rafraichir()
        menu.removeAllItems()
        // Active, sans effet : une ligne sans action est grisée et l'état de la liaison doit ressortir.
        menu.addItem(element(destination(), #selector(ligneSansAction)))
        menu.addItem(.separator())
        if let enCours, etat.visible {
            let titre = "● \(enCours.visio ? "Visio" : "Replay") · \(enCours.cible) · \(etat.temps(maintenant: maintenant()))"
            // Active, sans effet : comme les lignes d'état, elle ne doit pas être grisée.
            menu.addItem(element(titre, #selector(ligneSansAction)))
            for p in remplacants {
                let item = element(t(.menuReprendreSur, ["nom": p.nom, "pid": String(p.pid)]), #selector(reprendre))
                item.representedObject = p.pid
                menu.addItem(item)
            }
            menu.addItem(element(t(.menuArreter), #selector(arreterDepuisMenu), touche: "."))
        } else {
            menu.addItem(sousMenuVisio())
            menu.addItem(sousMenuReplay())
        }
        menu.addItem(.separator())
        if let confirmation {
            menu.addItem(element(confirmation, #selector(ligneSansAction)))
            if liaison != nil { menu.addItem(element(t(.actionSuivre), #selector(suivreLesTraitements))) }
        }
        for (fichier, etat) in etats.sorted(by: { $0.key < $1.key }) {
            menu.addItem(ligneDeCapture(fichier, etat))
        }
        if !file.captures.isEmpty {
            menu.addItem(element(t(.menuRenvoyerCaptures, ["n": String(file.captures.count)]), #selector(renvoyerALaMain)))
        }
        menu.addItem(sousMenuProjet())
        menu.addItem(element(t(liaison == nil ? .menuRelier : .menuChangerLiaison), #selector(relier)))
        if liaison != nil { menu.addItem(element(t(.menuOublier), #selector(oublierLaLiaison))) }
        menu.addItem(element(t(.menuOuvrirDossier), #selector(ouvrirDossier)))
        menu.addItem(element(t(.menuQuitter), #selector(quitter), touche: "q"))
    }

    private func t(_ cle: Cle, _ valeurs: [String: String] = [:]) -> String { Texte.phrase(cle, langue, valeurs) }

    /// Une capture : son état en phrase courte, le détail au survol. Une capture « prête » ouvre sa réunion.
    private func ligneDeCapture(_ fichier: String, _ etat: EtatCapture) -> NSMenuItem {
        let actifs = comptes?.actifs ?? 0
        let titre = Texte.etiquette(fichier, Texte.ligne(etat, actifs: actifs, langue), langue)
        let item: NSMenuItem
        if case .prete = etat, let prete = pretes[fichier] {
            item = element(titre, #selector(ouvrirReunion))
            item.representedObject = fichier
            item.toolTip = t(.menuOuvrirReunion, ["reunion": prete.reunion])
        } else {
            // Une ligne SANS action est grisée, et AppKit n'y montre pas toujours l'infobulle : action sans effet.
            item = element(titre, #selector(ligneSansAction))
            item.toolTip = detail(etat)
        }
        return item
    }

    /// Le détail au survol. Jamais le titre d'une réunion qui n'est pas la sienne : le serveur ne
    /// rend, avec ce jeton, que SES captures et le nombre des autres traitements.
    private func detail(_ etat: EtatCapture) -> String? {
        switch etat {
        case .attente(_, _, .occupe), .traitement(_, _, true):
            guard let comptes else { return nil }
            var lignes = comptes.captures.filter { $0.statut == "running" || $0.statut == "pending" }
                .map { $0.projet.isEmpty ? $0.titre : "\($0.titre) · \($0.projet)" }
            let autres = comptes.actifs - comptes.captures.filter { $0.statut == "running" }.count
            if autres > 0 { lignes.append(t(.detailAutres, ["n": String(autres)])) }
            return lignes.isEmpty ? nil : lignes.joined(separator: "\n")
        case .echecTraitement(let raison):
            return t(.enAttenteDetail, ["detail": raison])
        default:
            return nil
        }
    }

    private func element(_ titre: String, _ action: Selector, touche: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: titre, action: action, keyEquivalent: touche)
        item.target = self
        return item
    }

    private func destination() -> String {
        // La pastille : un libellé ET une forme (● liée, ○ hors ligne, ◌ vérification), jamais la couleur seule.
        let pastille = Texte.pastille(Texte.etatDeLaLiaison(lie: liaison != nil, joignable: joignable,
                                                                    derniereIssue: liaisonRefusee ? .jetonRefuse : nil), langue)
        guard let liaison else { return "\(pastille) — Memoglyph" }
        let hote = liaison.adresse.host ?? ""
        let lieu = ["127.0.0.1", "localhost", "::1"].contains(hote) ? t(.destinationCeMac) : hote
        let projet = projets.first { $0.id == projetChoisi }.map { Texte.nomDeProjet($0.nom, kind: $0.kind, langue) }
            ?? t(.destinationProjetParDefaut)
        return "\(pastille) — Memoglyph · \(lieu) · \(projet)"
    }

    /// Visio : les appels en cours en tête, puis une ligne par application ouverte qui a
    /// l'audio ouvert, puis les autres processus et tout le son du Mac. Le choix est calculé
    /// par `ChoixVisio` ; chaque ligne vise l'identifiant du parent, qui couvre ses auxiliaires.
    private func sousMenuVisio() -> NSMenuItem {
        let item = NSMenuItem(title: t(.menuDemarrerVisio), action: nil, keyEquivalent: "")
        let sous = NSMenu()
        do {
            let applications = NSWorkspace.shared.runningApplications
                .filter { $0.activationPolicy == .regular }
                .compactMap { a in a.bundleIdentifier.map { ($0, a.localizedName ?? $0) } }
            let menu = ChoixVisio.menu(try ProcessusAudio.lister(), applications: applications)
            if menu.appels.isEmpty {
                // Sans action, l'élément est grisé.
                sous.addItem(NSMenuItem(title: ChoixVisio.aucunAppel(langue), action: nil, keyEquivalent: ""))
            }
            for appel in menu.appels {
                let ligne = ligneVisio(appel)
                ligne.attributedTitle = NSAttributedString(
                    string: appel.libelle(langue), attributes: [.font: NSFont.boldSystemFont(ofSize: NSFont.systemFontSize)])
                ligne.image = NSImage(systemSymbolName: "phone.fill", accessibilityDescription: appel.libelle(langue))
                sous.addItem(ligne)
            }
            sous.addItem(.separator())
            menu.applications.forEach { sous.addItem(ligneVisio($0)) }
            if !menu.autres.isEmpty {
                let autres = NSMenuItem(title: t(.menuAutresProcessus), action: nil, keyEquivalent: "")
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
            sous.addItem(element(ChoixVisio.toutLeSon(langue), #selector(demarrerToutLeSon)))
        } catch {
            sous.addItem(NSMenuItem(title: t(.menuListeImpossible, ["detail": "\(error)"]), action: nil, keyEquivalent: ""))
        }
        item.submenu = sous
        return item
    }

    private func ligneVisio(_ l: LigneVisio) -> NSMenuItem {
        let ligne = element(l.libelle(langue), #selector(demarrerVisio))
        ligne.representedObject = [l.cible, ChoixVisio.nom(l, langue)]
        return ligne
    }

    /// Replay : les processus qui jouent en ce moment, un par PID. Un service partagé comme
    /// `com.apple.WebKit.GPU` sert plusieurs applications : n'en prendre qu'un.
    private func sousMenuReplay() -> NSMenuItem {
        let item = NSMenuItem(title: t(.menuDemarrerReplay), action: nil, keyEquivalent: "")
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
                sous.addItem(NSMenuItem(title: t(.menuRienNeJoue), action: nil, keyEquivalent: ""))
            }
        } catch {
            sous.addItem(NSMenuItem(title: t(.menuListeImpossible, ["detail": "\(error)"]), action: nil, keyEquivalent: ""))
        }
        item.submenu = sous
        return item
    }

    /// Le projet d'envoi : ceux du compte ; le compagnon n'en crée jamais.
    private func sousMenuProjet() -> NSMenuItem {
        let item = NSMenuItem(title: t(.menuProjetEnvoi), action: nil, keyEquivalent: "")
        let sous = NSMenu()
        let defaut = element(t(.menuProjetParDefaut), #selector(choisirProjet))
        defaut.state = projetChoisi == nil ? .on : .off
        sous.addItem(defaut)
        for p in projets {
            let ligne = element(Texte.nomDeProjet(p.nom, kind: p.kind, langue), #selector(choisirProjet))
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
        let nom = ChoixVisio.toutLeSon(langue)
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
    @objc private func ouvrirReunion(_ item: NSMenuItem) {
        guard let liaison, let fichier = item.representedObject as? String, let prete = pretes[fichier] else { return }
        NSWorkspace.shared.open(ClientMemoglyph.lien(adresse: liaison.adresse, projet: prete.projet,
                                                     reunion: prete.meetingId))
    }
    @objc private func ligneSansAction() {}
    /// Retire l'adresse et le jeton du trousseau (le service de CE compagnon : celui de l'essai reste à part).
    @objc private func oublierLaLiaison() {
        Trousseau.oublier()
        liaison = nil
        joignable = nil
        liaisonRefusee = false
        projets = []
        comptes = nil
    }
    /// « Suivre » : la vue des traitements, dans l'interface.
    @objc private func suivreLesTraitements() {
        guard let liaison else { return }
        NSWorkspace.shared.open(ClientMemoglyph.lienSuivre(adresse: liaison.adresse))
    }
    @objc private func quitter() { NSApp.terminate(nil) }

    // MARK: - Démarrer

    private func demander(nom: String, visio: Bool, puis: @escaping () -> Void) {
        let rappels = Rappels.auDemarrage(visio: visio, langue) + [t(.rappelDestination, ["lieu": destination()])]
        guard confirmer(t(.dlgDemarrerTitre, ["nom": nom, "mode": visio ? "Visio" : "Replay"]),
                        rappels.map { "• " + $0 }.joined(separator: "\n"), t(.dlgDemarrer)) else { return }
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
                        self.informer(self.t(.dlgMicroRefuseTitre), self.t(.dlgMicroRefuse))
                    case .enAttente:
                        self.informer(self.t(.dlgMicroAttenteTitre), self.t(.dlgMicroAttente))
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
            return informer(t(.dlgCaptureImpossible), "\(error)")
        }
        guard global || !vises.isEmpty else {
            return informer(t(.dlgPlusAudioTitre, ["nom": nom]), t(.dlgPlusAudio))
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
            return informer(t(.dlgCaptureImpossible), "\(error)")
        }
        self.enregistreur = enregistreur
        self.moniteur = moniteur
        enCours = (nom, visio, fichier, suivi)
        confirmation = nil
        confirmationDe = nil
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
            return informer(t(.dlgArretErreurTitre),
                            t(.dlgArretErreur, ["detail": "\(error)", "chemin": enCours.fichier.path]),
                            fichier: enCours.fichier)
        }
        let duree = String(format: "%.0f s", bilan.duree)
        // Un canal entièrement nul n'est pas un enregistrement réussi (voir memoglyph-capture) :
        // il n'est pas envoyé.
        if bilan.creteSysteme == 0 {
            return informer(t(.dlgMuetTitre, ["nom": enCours.cible]), t(.dlgMuet, ["duree": duree]),
                            fichier: enCours.fichier)
        }
        if let creteMicro = bilan.creteMicro, creteMicro == 0 {
            return informer(t(.dlgMicroMuetTitre), t(.dlgMicroMuet, ["duree": duree]), fichier: enCours.fichier)
        }
        let capture = Capture(fichier: enCours.fichier.lastPathComponent, visio: enCours.visio,
                              application: enCours.suivi?.nom ?? enCours.cible)
        mettreEnFile(capture)
        let suite = t(liaison == nil ? .dlgSuiteSansLiaison : .dlgSuiteEnvoi)
        if bilanModal {
            informer(t(.dlgSuccesTitre, ["duree": duree]), enCours.fichier.path + "\n\n" + suite, fichier: enCours.fichier)
        }
        envoyer(capture)
    }

    // MARK: - Memoglyph

    /// Santé de Memoglyph et projets du compte, à l'ouverture du menu ; servis au suivant.
    private func rafraichir() {
        guard let liaison else { joignable = nil; projets = []; return }
        Liaison.sante(liaison.adresse) { ok in MainActor.assumeIsolated { self.joignable = ok } }
        Liaison.projets(liaison.adresse, jeton: liaison.jeton) { resultat in
            MainActor.assumeIsolated {
                switch resultat {
                case .success(let liste): self.projets = liste; self.liaisonRefusee = false
                case .failure(let e): if e.issue == .jetonRefuse { self.liaisonRefusee = true }
                }
            }
        }
        Liaison.comptes(liaison.adresse, jeton: liaison.jeton) { resultat in
            MainActor.assumeIsolated { if case .success(let c) = resultat { self.comptes = c } }
        }
    }

    @objc private func relier() {
        let adresse = NSTextField(string: liaison?.adresse.absoluteString ?? "http://127.0.0.1:8770")
        let jeton = NSSecureTextField(string: "")
        jeton.placeholderString = "mgj_…"
        for champ in [adresse, jeton] { champ.widthAnchor.constraint(equalToConstant: 300).isActive = true }
        let pile = NSStackView(views: [NSTextField(labelWithString: t(.dlgAdresse)), adresse,
                                       NSTextField(labelWithString: t(.dlgJeton)), jeton])
        pile.orientation = .vertical
        pile.alignment = .leading
        pile.frame = NSRect(x: 0, y: 0, width: 300, height: 110)
        let alerte = NSAlert()
        alerte.messageText = t(.dlgRelierTitre)
        alerte.informativeText = t(.dlgRelierTexte)
        alerte.accessoryView = pile
        alerte.addButton(withTitle: t(.dlgRelier))
        alerte.addButton(withTitle: t(.dlgAnnuler))
        if liaison != nil { alerte.addButton(withTitle: t(.dlgOublier)) }
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
            return informer(t(.dlgAdresseInvalideTitre), t(.dlgAdresseInvalide))
        }
        let secret = jeton.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard ClientMemoglyph.jetonValide(secret) else {
            return informer(t(.dlgJetonInvalideTitre), t(.dlgJetonInvalide))
        }
        // Le jeton n'est rangé qu'après avoir ouvert la liste des projets du compte.
        Liaison.projets(url, jeton: secret) { resultat in
            MainActor.assumeIsolated {
                switch resultat {
                case .failure(let e):
                    self.informer(self.t(.dlgLiaisonRefusee), Texte.raison(e.issue, self.langue))
                case .success(let liste):
                    let statut = Trousseau.ecrire(adresse: url, jeton: secret)
                    guard statut == errSecSuccess else {
                        return self.informer(self.t(.dlgTrousseauTitre), Trousseau.message(statut))
                    }
                    self.liaison = (url, secret)
                    self.liaisonRefusee = false
                    self.projets = liste
                    self.joignable = true
                    if let choisi = self.projetChoisi, !liste.contains(where: { $0.id == choisi }) { self.projetChoisi = nil }
                    self.rafraichir()
                    self.renvoyer()
                }
            }
        }
    }

    /// Le geste manuel : les compteurs repartent de zéro.
    @objc private func renvoyerALaMain() {
        renvois.removeAll()
        renvoyer()
    }

    /// Chaque capture de la file repart (une seule fois à la fois : `envoyer` ignore celle qui part déjà).
    private func renvoyer() { file.captures.forEach { envoyer($0) } }

    private func mettreEnFile(_ capture: Capture) {
        file.ajouter(capture)
        enregistrerFile()
    }

    private func enregistrerFile() {
        try? FileManager.default.createDirectory(at: Compagnon.dossier, withIntermediateDirectories: true)
        try? file.donnees().write(to: Compagnon.fichierFile, options: .atomic)
    }

    /// Envoie une capture et suit son travail. Elle ne quitte la file qu'une fois reçue. Refusée faute de
    /// place ou injoignable, elle repart seule (`Renvoi.decider`) ; jamais perdue : même après le dernier
    /// essai elle reste dans la file, et le menu le dit en une phrase.
    private func envoyer(_ capture: Capture) {
        guard let liaison else { etats[capture.fichier] = .sansLiaison; return }
        if case .envoi? = etats[capture.fichier] { return }
        minuteries.removeValue(forKey: capture.fichier)?.cancel()
        etats[capture.fichier] = .envoi
        let projet = projetChoisi
        Liaison.envoyer(capture, dossier: Compagnon.dossier, projet: projet,
                        adresse: liaison.adresse, jeton: liaison.jeton) { issue in
            MainActor.assumeIsolated { self.apresEnvoi(capture, issue: issue, projet: projet) }
        }
    }

    private func apresEnvoi(_ capture: Capture, issue: Issue, projet: String?) {
        let fichier = capture.fichier
        switch Renvoi.decider(issue, renvoisFaits: renvois[fichier] ?? 0) {
        case .envoyee:
            file.retirer(fichier)
            enregistrerFile()
            renvois[fichier] = nil
            let nomProjet = projets.first { $0.id == projet }.map { Texte.nomDeProjet($0.nom, kind: $0.kind, langue) }
            switch issue {
            case .accepte(let travail):
                etats[fichier] = .recue
                // Le nom du projet vient du traitement lui-même au premier suivi (le projet par défaut du compte
                // n'est pas dans le réglage) ; d'ici là, « Capture reçue ».
                confirmationDe = (fichier, nomProjet)
                confirmation = nomProjet.map { Texte.confirmation(projet: $0, autresEnCours: 0, langue) }
                suivre(travail, fichier: fichier, projet: projet)
            case .dejaRecue(let reunion, let travail, let meetingId, let projetReunion):
                // Le serveur l'avait déjà : pas de seconde réunion.
                if let reunion {
                    pretes[fichier] = (reunion, projetReunion ?? projet, meetingId)
                    etats[fichier] = .prete(avertissements: 0)
                } else if let travail {
                    etats[fichier] = .recue
                    suivre(travail, fichier: fichier, projet: projet)
                }
            default:
                break
            }
            rafraichir()
        case .reessayer(let apres, let cause):
            let fait = renvois[fichier] ?? 0
            renvois[fichier] = fait + 1
            etats[fichier] = .attente(essai: fait + 1, dans: apres, cause: cause)
            rafraichir()   // le nombre de traitements en cours, pour « en attente : N »
            let travail = DispatchWorkItem { [weak self] in
                MainActor.assumeIsolated {
                    guard let self else { return }
                    self.minuteries[fichier] = nil
                    if case .attente? = self.etats[fichier] { self.envoyer(capture) }
                }
            }
            minuteries[fichier] = travail
            DispatchQueue.main.asyncAfter(deadline: .now() + apres, execute: travail)
        case .abandonner(let raison):
            etats[fichier] = .nonEnvoyee(raison)
            if raison == .jetonRefuse { liaisonRefusee = true }
        }
    }

    private func effacerConfirmation(de fichier: String) {
        guard confirmationDe?.fichier == fichier else { return }
        confirmation = nil
        confirmationDe = nil
    }

    private func suivre(_ travail: String, fichier: String, projet: String?) {
        guard let liaison else { return }
        Liaison.travail(travail, adresse: liaison.adresse, jeton: liaison.jeton) { etat in
            MainActor.assumeIsolated {
                switch etat {
                case .enCours(let progression, _, let code, let enAttente, let projetNom):
                    self.etats[fichier] = .traitement(progression: progression, code: code, enAttente: enAttente)
                    // « En attente : un traitement est déjà en cours » : d'après CE traitement, pas un relevé ancien.
                    if let c = self.confirmationDe, c.fichier == fichier {
                        let nom = projetNom.map { Texte.nomDeProjet($0, self.langue) } ?? c.projet
                        self.confirmationDe = (fichier, nom)
                        if let nom {
                            self.confirmation = Texte.confirmation(projet: nom, autresEnCours: enAttente ? 1 : 0, self.langue)
                        }
                    }
                    DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
                        MainActor.assumeIsolated { self.suivre(travail, fichier: fichier, projet: projet) }
                    }
                case .fini(let reunion, let avertissements, let meetingId, let projetReunion, _):
                    self.etats[fichier] = .prete(avertissements: avertissements.count)
                    self.pretes[fichier] = (reunion, projetReunion ?? projet, meetingId)
                    self.effacerConfirmation(de: fichier)
                case .echec(let raison):
                    self.etats[fichier] = .echecTraitement(raison)
                    self.effacerConfirmation(de: fichier)
                case .perdu(let issue):
                    self.etats[fichier] = .suiviInterrompu(issue)
                    if issue == .jetonRefuse { self.liaisonRefusee = true }
                    self.effacerConfirmation(de: fichier)
                }
            }
        }
    }

    // MARK: - Outils

    private func maintenant() -> Double { Date().timeIntervalSinceReferenceDate }

    private func dessinerIcone() {
        let actif = enregistreur != nil
        icone.button?.image = NSImage(systemSymbolName: actif ? "record.circle.fill" : "waveform.circle",
                                      accessibilityDescription: actif ? "Memoglyph — \(t(.menuEnregistrement))" : "Memoglyph")
        icone.button?.contentTintColor = actif ? .systemRed : nil
    }

    private func confirmer(_ titre: String, _ texte: String, _ bouton: String) -> Bool {
        let alerte = NSAlert()
        alerte.messageText = titre
        alerte.informativeText = texte
        alerte.addButton(withTitle: bouton)
        alerte.addButton(withTitle: t(.dlgAnnuler))
        NSApp.activate(ignoringOtherApps: true)
        return alerte.runModal() == .alertFirstButtonReturn
    }

    private func informer(_ titre: String, _ texte: String, fichier: URL? = nil) {
        let alerte = NSAlert()
        alerte.messageText = titre
        alerte.informativeText = texte
        alerte.addButton(withTitle: t(.dlgOK))
        if fichier != nil { alerte.addButton(withTitle: t(.dlgFinder)) }
        NSApp.activate(ignoringOtherApps: true)
        if alerte.runModal() == .alertSecondButtonReturn, let fichier {
            NSWorkspace.shared.activateFileViewerSelecting([fichier])
        }
    }
}
