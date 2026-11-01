import CaptureCore
import XCTest

/// Le menu « Démarrer en Visio », calculé sur quatre relevés réels du menu (sans appel,
/// Zoom, Teams, Google Meet dans Chrome), recopiés ligne pour ligne.
///
/// Deux informations manquent aux relevés et sont posées par HYPOTHÈSE :
/// - l'entrée micro (kAudioProcessPropertyIsRunningInput), invisible dans l'ancien menu :
///   elle est donnée, capture par capture, par la liste `micro` de `relevé` ;
/// - les applications ouvertes (NSWorkspace, `activationPolicy == .regular`) : la liste
///   `ouvertesBase` ci-dessous, complétée capture par capture.
final class ChoixVisioTests: XCTestCase {
    // MARK: - Relevés

    /// 1. Sans appel.
    private let sansAppel = """
    AccessibilitySettingsWidgetExtension (com.apple.AccessibilitySettingsWidgetExtension)
    Brave Browser Helper (com.brave.Browser.helper)
    Claude Helper (com.anthropic.claudefordesktop.helper)
    Codex (Service) (com.openai.codex.helper)
    ContinuityCaptureAgent (com.apple.cmio.ContinuityCaptureAgent)
    ControlCenter (com.apple.controlcenter)
    DictationIM (com.apple.inputmethod.ironwood)
    PerfPowerServices ()
    PowerChime (com.apple.PowerChime)
    QuickLookUIService (com.apple.quicklook.QuickLookUIService)
    Safari (com.apple.Safari)
    ServiceExtension (net.whatsapp.WhatsApp.ServiceExtension)
    Siri (com.apple.Siri)
    SiriSetupSettingsIntents (com.apple.SiriSetup.SiriSetupSettingsIntents)
    SkyComputerUseService (com.openai.sky.CUAService)
    Terminal (com.apple.Terminal)
    WhatsApp (net.whatsapp.WhatsApp)
    assistantd (com.apple.assistantd)
    audioaccessoryd (com.apple.cloudpaird)
    audiomxd (com.apple.audiomxd)
    avconferenced (com.apple.avconferenced)
    callservicesd (com.apple.TelephonyUtilities)
    com.apple.WebKit.GPU (com.apple.WebKit.GPU)
    com.apple.audio.Core-Audio-Driver-Service.helper (com.apple.audio.Core-Audio-Driver-Service.helper)
    corespeechd (com.apple.CoreSpeech)
    heard (com.apple.accessibility.heard)
    mediaremoted (com.apple.mediaremoted)
    memoglyph-compagnon (org.memoglyph.compagnon)
    systemsoundserverd (systemsoundserverd)
    systemstats ()
    universalaccessd (com.apple.universalaccessd)
    """

    /// 2. Zoom en appel.
    private let zoom = """
    ● zoom.us (us.zoom.xos)
    AccessibilitySettingsWidgetExtension (com.apple.AccessibilitySettingsWidgetExtension)
    Brave Browser Helper (com.brave.Browser.helper)
    Claude Helper (com.anthropic.claudefordesktop.helper)
    Codex (Service) (com.openai.codex.helper)
    ContinuityCaptureAgent (com.apple.cmio.ContinuityCaptureAgent)
    ControlCenter (com.apple.controlcenter)
    DictationIM (com.apple.inputmethod.ironwood)
    PerfPowerServices ()
    PowerChime (com.apple.PowerChime)
    QuickLookUIService (com.apple.quicklook.QuickLookUIService)
    Safari (com.apple.Safari)
    ServiceExtension (net.whatsapp.WhatsApp.ServiceExtension)
    Siri (com.apple.Siri)
    SiriSetupSettingsIntents (com.apple.SiriSetup.SiriSetupSettingsIntents)
    SkyComputerUseService (com.openai.sky.CUAService)
    Terminal (com.apple.Terminal)
    WhatsApp (net.whatsapp.WhatsApp)
    assistantd (com.apple.assistantd)
    audioaccessoryd (com.apple.cloudpaird)
    audiomxd (com.apple.audiomxd)
    avconferenced (com.apple.avconferenced)
    callservicesd (com.apple.TelephonyUtilities)
    com.apple.WebKit.GPU (com.apple.WebKit.GPU)
    com.apple.audio.Core-Audio-Driver-Service.helper (com.apple.audio.Core-Audio-Driver-Service.helper)
    corespeechd (com.apple.CoreSpeech)
    heard (com.apple.accessibility.heard)
    mediaremoted (com.apple.mediaremoted)
    memoglyph-compagnon (org.memoglyph.compagnon)
    systemsoundserverd (systemsoundserverd)
    systemstats ()
    universalaccessd (com.apple.universalaccessd)
    caphost (us.zoom.caphost)
    """

    /// 3. Teams en appel.
    private let teams = """
    ● Microsoft Teams ModuleHost (com.microsoft.teams2.modulehost)
    AccessibilitySettingsWidgetExtension (com.apple.AccessibilitySettingsWidgetExtension)
    Brave Browser Helper (com.brave.Browser.helper)
    Claude Helper (com.anthropic.claudefordesktop.helper)
    Codex (Service) (com.openai.codex.helper)
    ContinuityCaptureAgent (com.apple.cmio.ContinuityCaptureAgent)
    ControlCenter (com.apple.controlcenter)
    DictationIM (com.apple.inputmethod.ironwood)
    PerfPowerServices ()
    PowerChime (com.apple.PowerChime)
    QuickLookUIService (com.apple.quicklook.QuickLookUIService)
    Safari (com.apple.Safari)
    ServiceExtension (net.whatsapp.WhatsApp.ServiceExtension)
    Siri (com.apple.Siri)
    SiriSetupSettingsIntents (com.apple.SiriSetup.SiriSetupSettingsIntents)
    SkyComputerUseService (com.openai.sky.CUAService)
    Terminal (com.apple.Terminal)
    WhatsApp (net.whatsapp.WhatsApp)
    assistantd (com.apple.assistantd)
    audioaccessoryd (com.apple.cloudpaird)
    audiomxd (com.apple.audiomxd)
    avconferenced (com.apple.avconferenced)
    callservicesd (com.apple.TelephonyUtilities)
    com.apple.WebKit.GPU (com.apple.WebKit.GPU)
    com.apple.audio.Core-Audio-Driver-Service.helper (com.apple.audio.Core-Audio-Driver-Service.helper)
    corespeechd (com.apple.CoreSpeech)
    heard (com.apple.accessibility.heard)
    mediaremoted (com.apple.mediaremoted)
    memoglyph-compagnon (org.memoglyph.compagnon)
    systemsoundserverd (systemsoundserverd)
    systemstats ()
    universalaccessd (com.apple.universalaccessd)
    Microsoft Teams WebView Helper (com.microsoft.teams2.helper)
    caphost (us.zoom.caphost)
    zoom.us (us.zoom.xos)
    """

    /// 4. Google Meet dans Chrome.
    private let meet = """
    ● Google Chrome Helper (Aperitif) (com.google.Chrome.helper)
    AccessibilitySettingsWidgetExtension (com.apple.AccessibilitySettingsWidgetExtension)
    Brave Browser Helper (com.brave.Browser.helper)
    Claude Helper (com.anthropic.claudefordesktop.helper)
    Codex (Service) (com.openai.codex.helper)
    ContinuityCaptureAgent (com.apple.cmio.ContinuityCaptureAgent)
    ControlCenter (com.apple.controlcenter)
    DictationIM (com.apple.inputmethod.ironwood)
    PerfPowerServices ()
    PowerChime (com.apple.PowerChime)
    QuickLookUIService (com.apple.quicklook.QuickLookUIService)
    Safari (com.apple.Safari)
    ServiceExtension (net.whatsapp.WhatsApp.ServiceExtension)
    Siri (com.apple.Siri)
    SiriSetupSettingsIntents (com.apple.SiriSetup.SiriSetupSettingsIntents)
    SkyComputerUseService (com.openai.sky.CUAService)
    Terminal (com.apple.Terminal)
    WhatsApp (net.whatsapp.WhatsApp)
    assistantd (com.apple.assistantd)
    audioaccessoryd (com.apple.cloudpaird)
    audiomxd (com.apple.audiomxd)
    avconferenced (com.apple.avconferenced)
    callservicesd (com.apple.TelephonyUtilities)
    com.apple.WebKit.GPU (com.apple.WebKit.GPU)
    com.apple.audio.Core-Audio-Driver-Service.helper (com.apple.audio.Core-Audio-Driver-Service.helper)
    corespeechd (com.apple.CoreSpeech)
    heard (com.apple.accessibility.heard)
    mediaremoted (com.apple.mediaremoted)
    memoglyph-compagnon (org.memoglyph.compagnon)
    systemsoundserverd (systemsoundserverd)
    systemstats ()
    universalaccessd (com.apple.universalaccessd)
    caphost (us.zoom.caphost)
    zoom.us (us.zoom.xos)
    """

    // MARK: - Hypothèses

    /// HYPOTHÈSE : applications ouvertes au premier relevé. Brave, Safari, WhatsApp et Codex
    /// (`com.openai.codex`, parent de `com.openai.codex.helper`) ; Claude (parent de
    /// `com.anthropic.claudefordesktop.helper`) et le Terminal, ouverts eux aussi puisque
    /// leurs processus audio figurent au relevé ; le Finder, sans processus audio. Le
    /// compagnon, `LSUIElement`, n'est pas `.regular`.
    private let ouvertesBase: [(String, String)] = [
        ("com.apple.finder", "Finder"),
        ("com.brave.Browser", "Brave Browser"),
        ("com.apple.Safari", "Safari"),
        ("net.whatsapp.WhatsApp", "WhatsApp"),
        ("com.openai.codex", "Codex"),
        ("com.anthropic.claudefordesktop", "Claude"),
        ("com.apple.Terminal", "Terminal"),
    ]
    /// HYPOTHÈSE : Zoom reste ouvert après son appel (il figure encore aux relevés 3 et 4).
    private let appZoom = ("us.zoom.xos", "zoom.us")
    /// HYPOTHÈSE : Teams est fermé au relevé 4 (aucun de ses processus n'y figure).
    private let appTeams = ("com.microsoft.teams2", "Microsoft Teams")
    private let appChrome = ("com.google.Chrome", "Google Chrome")

    /// Lit un relevé : `● ` = joue ; le dernier groupe entre parenthèses est l'identifiant
    /// (vide pour `()`). `micro` : les noms des processus qui prennent le micro (HYPOTHÈSE).
    /// `joue` : des noms qui jouent en plus de ceux marqués.
    private func releve(_ texte: String, micro: Set<String> = [], joue enPlus: Set<String> = []) -> [ProcessusDecrit] {
        texte.split(separator: "\n").enumerated().map { rang, ligne in
            var ligne = String(ligne)
            let marque = ligne.hasPrefix("● ")
            if marque { ligne.removeFirst(2) }
            let coupure = ligne.range(of: " (", options: .backwards)!
            let nom = String(ligne[..<coupure.lowerBound])
            let identifiant = String(ligne[coupure.upperBound...].dropLast())
            let pid = Int32(100 + rang)
            return ProcessusDecrit(objet: UInt32(pid), pid: pid, nom: nom, identifiant: identifiant,
                                   joue: marque || enPlus.contains(nom), prendMicro: micro.contains(nom))
        }
    }

    private func noms(_ lignes: [LigneVisio]) -> [String] { lignes.map(\.nom) }
    private func ligne(_ nom: String, _ lignes: [LigneVisio]) -> LigneVisio? { lignes.first { $0.nom == nom } }

    // MARK: - Les quatre relevés

    /// Relevé 1. HYPOTHÈSE micro : corespeechd écoute (« Dis Siri »), sans jouer.
    func testSansAppel() {
        let menu = ChoixVisio.menu(releve(sansAppel, micro: ["corespeechd"]), applications: ouvertesBase)
        XCTAssertEqual(menu.appels, [])
        XCTAssertEqual(noms(menu.applications),
                       ["Brave Browser", "Claude", "Codex", "Safari (son des pages web)", "Terminal", "WhatsApp"])
        XCTAssertEqual(ligne("Brave Browser", menu.applications)?.cible, "com.brave.Browser")
        XCTAssertEqual(ligne("Codex", menu.applications)?.cible, "com.openai.codex")
        XCTAssertEqual(ligne("WhatsApp", menu.applications)?.cible, "net.whatsapp.WhatsApp")
        XCTAssertEqual(ligne("Safari (son des pages web)", menu.applications)?.cible, "com.apple.WebKit.GPU")
        XCTAssertFalse(menu.applications.contains { $0.joue })
        // Démons et processus sans application : sous « Autres processus… », pas au premier niveau.
        for demon in ["audiomxd", "corespeechd", "SkyComputerUseService", "PerfPowerServices"] {
            XCTAssertNil(ligne(demon, menu.applications), demon)
            XCTAssertNotNil(ligne(demon, menu.autres), demon)
        }
        XCTAssertEqual(ligne("audiomxd", menu.autres)?.cible, "com.apple.audiomxd")
        XCTAssertEqual(ligne("PerfPowerServices", menu.autres)?.cible, "PerfPowerServices")
        // Un auxiliaire rattaché à une application ouverte ne reparaît pas dans les autres.
        for auxiliaire in ["Brave Browser Helper", "ServiceExtension", "Codex (Service)", "com.apple.WebKit.GPU", "Safari"] {
            XCTAssertNil(ligne(auxiliaire, menu.autres), auxiliaire)
        }
        // Le compagnon ne se propose pas lui-même.
        XCTAssertNil(ligne("memoglyph-compagnon", menu.autres))
        XCTAssertEqual(menu.autres.count, 22)
    }

    /// Relevé 2 : appel sur le parent. HYPOTHÈSE micro : zoom.us lui-même prend le micro.
    func testZoomAppelSurLeParent() {
        let menu = ChoixVisio.menu(releve(zoom, micro: ["zoom.us", "corespeechd"]),
                                   applications: ouvertesBase + [appZoom])
        XCTAssertEqual(menu.appels, [LigneVisio(nom: "zoom.us", cible: "us.zoom.xos", joue: true, enAppel: true)])
        XCTAssertEqual(menu.appels.first?.libelle, "Appel en cours : zoom.us")
        XCTAssertNil(ligne("zoom.us", menu.applications))
        XCTAssertEqual(menu.applications.count, 6)
        // caphost n'est pas un auxiliaire de `us.zoom.xos` (préfixe différent).
        XCTAssertEqual(ligne("caphost", menu.autres)?.cible, "us.zoom.caphost")
    }

    /// Relevé 3 : appel réparti sur deux auxiliaires. HYPOTHÈSE micro : le WebView Helper
    /// prend le micro, le ModuleHost joue.
    func testTeamsAppelRepartiSurDeuxAuxiliaires() {
        let menu = ChoixVisio.menu(releve(teams, micro: ["Microsoft Teams WebView Helper"]),
                                   applications: ouvertesBase + [appZoom, appTeams])
        XCTAssertEqual(menu.appels,
                       [LigneVisio(nom: "Microsoft Teams", cible: "com.microsoft.teams2", joue: true, enAppel: true)])
        // Zoom reste proposé, sans jouer ; aucun auxiliaire de Teams n'est une ligne.
        XCTAssertEqual(ligne("zoom.us", menu.applications),
                       LigneVisio(nom: "zoom.us", cible: "us.zoom.xos", joue: false, enAppel: false))
        XCTAssertFalse((menu.applications + menu.autres).contains { $0.cible.hasPrefix("com.microsoft.teams2.") })
    }

    /// Relevé 4 : Meet dans Chrome. HYPOTHÈSE micro : le même auxiliaire (le service audio
    /// de Chrome) joue et prend le micro.
    func testMeetDansChrome() {
        let menu = ChoixVisio.menu(releve(meet, micro: ["Google Chrome Helper (Aperitif)"]),
                                   applications: ouvertesBase + [appZoom, appChrome])
        XCTAssertEqual(menu.appels,
                       [LigneVisio(nom: "Google Chrome", cible: "com.google.Chrome", joue: true, enAppel: true)])
        XCTAssertEqual(noms(menu.applications),
                       ["Brave Browser", "Claude", "Codex", "Safari (son des pages web)", "Terminal", "WhatsApp", "zoom.us"])
    }

    // MARK: - Cas

    /// Un navigateur qui joue sans micro (une vidéo) n'est pas un appel : il passe en tête,
    /// marqué « ● ».
    func testNavigateurQuiJoueSansMicroNEstPasUnAppel() {
        let menu = ChoixVisio.menu(releve(sansAppel, joue: ["Brave Browser Helper"]), applications: ouvertesBase)
        XCTAssertEqual(menu.appels, [])
        XCTAssertEqual(menu.applications.first,
                       LigneVisio(nom: "Brave Browser", cible: "com.brave.Browser", joue: true, enAppel: false))
        XCTAssertEqual(menu.applications.first?.libelle, "● Brave Browser")
        XCTAssertEqual(menu.applications.dropFirst().first?.libelle, "Claude")
    }

    /// Le son d'une application et le micro d'une autre ne font pas un appel.
    func testSonEtMicroDeDeuxApplicationsNeFontPasUnAppel() {
        let menu = ChoixVisio.menu(releve(teams, micro: ["zoom.us"], joue: []),
                                   applications: ouvertesBase + [appZoom, appTeams])
        XCTAssertEqual(menu.appels, [])
        XCTAssertEqual(ligne("Microsoft Teams", menu.applications)?.joue, true)
        XCTAssertEqual(ligne("zoom.us", menu.applications)?.joue, false)
    }

    /// Le micro seul (un démon qui écoute, une application muette) n'est pas un appel.
    func testMicroSansSonNEstPasUnAppel() {
        let menu = ChoixVisio.menu(releve(sansAppel, micro: ["WhatsApp", "corespeechd"]), applications: ouvertesBase)
        XCTAssertEqual(menu.appels, [])
    }

    /// Deux appels : une ligne chacun, par nom.
    func testDeuxAppels() {
        let processus = releve(teams, micro: ["Microsoft Teams WebView Helper", "zoom.us"], joue: ["zoom.us"])
        let menu = ChoixVisio.menu(processus, applications: ouvertesBase + [appZoom, appTeams])
        XCTAssertEqual(menu.appels.map(\.libelle), ["Appel en cours : Microsoft Teams", "Appel en cours : zoom.us"])
        XCTAssertEqual(menu.appels.map(\.cible), ["com.microsoft.teams2", "us.zoom.xos"])
        XCTAssertNil(ligne("zoom.us", menu.applications))
        XCTAssertNil(ligne("Microsoft Teams", menu.applications))
    }

    /// La cible d'une ligne couvre bien tous les processus de son groupe dans `choisir`.
    func testLaCibleCouvreLesAuxiliaires() {
        let processus = releve(teams, micro: ["Microsoft Teams WebView Helper"])
        let menu = ChoixVisio.menu(processus, applications: ouvertesBase + [appZoom, appTeams])
        let vises = ProcessusDecrit.choisir(.identifiant(menu.appels[0].cible), parmi: processus).map(\.nom)
        XCTAssertEqual(Set(vises), ["Microsoft Teams ModuleHost", "Microsoft Teams WebView Helper"])
    }

    /// Safari : son web dans `com.apple.WebKit.GPU`, une seule ligne. Sans processus WebKit,
    /// la ligne vise Safari lui-même ; sans Safari ouvert, WebKit va dans les autres.
    func testSafari() {
        let sansWebKit = releve(sansAppel).filter { $0.identifiant != "com.apple.WebKit.GPU" }
        let menu = ChoixVisio.menu(sansWebKit, applications: ouvertesBase)
        XCTAssertEqual(ligne("Safari", menu.applications)?.cible, "com.apple.Safari")
        XCTAssertNil(ligne("Safari (son des pages web)", menu.applications))

        let sansSafari = ChoixVisio.menu(releve(sansAppel), applications: ouvertesBase.filter { $0.1 != "Safari" })
        XCTAssertNil(ligne("Safari (son des pages web)", sansSafari.applications))
        XCTAssertEqual(ligne("com.apple.WebKit.GPU", sansSafari.autres)?.cible, "com.apple.WebKit.GPU")

        // Meet dans Safari : WebKit joue et prend le micro.
        let appel = ChoixVisio.menu(releve(sansAppel, micro: ["com.apple.WebKit.GPU"], joue: ["com.apple.WebKit.GPU"]),
                                    applications: ouvertesBase)
        XCTAssertEqual(appel.appels.map(\.cible), ["com.apple.WebKit.GPU"])
    }

    /// Une application ouverte sans processus audio n'a pas de ligne ; un doublon de
    /// NSWorkspace non plus.
    func testApplicationSansAudioEtDoublon() {
        let menu = ChoixVisio.menu(releve(sansAppel), applications: ouvertesBase + [("com.brave.Browser", "Brave")])
        XCTAssertNil(ligne("Finder", menu.applications))
        XCTAssertEqual(menu.applications.filter { $0.cible == "com.brave.Browser" }.count, 1)
    }

    /// « Tout le son du Mac » exclut le compagnon : par son PID, ou par son identifiant.
    func testToutLeSonExclutLeCompagnon() {
        let processus = releve(sansAppel)
        let soi = processus.first { $0.nom == "memoglyph-compagnon" }!
        XCTAssertEqual(ChoixVisio.exclusDuTout(processus, pid: soi.pid), [soi])
        XCTAssertEqual(ChoixVisio.exclusDuTout(processus, pid: 1), [soi])
        XCTAssertEqual(ChoixVisio.toutLeSon, "Tout le son du Mac")
        XCTAssertEqual(ChoixVisio.aucunAppel, "Aucun appel détecté : lancez l'appel, puis rouvrez ce menu")
    }
}
