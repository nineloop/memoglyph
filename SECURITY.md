# Security Policy

## Scope and threat model

Memoglyph is a **local, self-hosted** application. It runs on your machine and stores
its data (audio, transcripts, indexes, accounts) on your machine. Sign-in is required
everywhere, locally included, but the security of the data still rests on the security
of the machine it runs on: anyone with access to the data folder can read it.

Each point below names the test (or the line) that holds it.

### Accounts and sessions

- **Sign-in is required for every `/api` route**, except a short public list (health,
  sign-in state, first-account setup, sign-in, sign-out). `tests/integration/test_auth_api.py`:
  `test_toute_route_api_exige_une_session_sauf_les_publiques`,
  `test_les_routes_publiques_existent_et_restent_peu_nombreuses`.
- **No default account, no open sign-up.** The first account is created with a one-time
  setup code drawn at start-up while no account exists; only an administrator creates
  further accounts. `test_installation_neuve_puis_connexion_complete`,
  `test_aucune_route_d_inscription_libre`, `test_seul_un_administrateur_cree_un_compte`.
- **Passwords** are hashed with Argon2id and never stored in clear
  (`tests/unit/test_auth.py`: `test_le_mot_de_passe_n_est_stocke_qu_en_argon2id`).
- **Sessions** live in an `HttpOnly` cookie (`Secure` over HTTPS); only the SHA-256
  digest of the session token is stored, and a session can be revoked or expires after
  14 days. `test_la_session_ne_stocke_que_l_empreinte_du_jeton`,
  `test_une_session_revoquee_ou_expiree_ne_vaut_plus`, `test_le_cookie_est_secure_en_https`.
- **Repeated failed sign-ins are slowed down** (from the fifth failure, a doubling delay
  capped at 15 minutes), without revealing whether the account exists.
  `test_cinq_echecs_bloquent_sans_reveler_si_le_compte_existe`,
  `test_le_delai_est_plafonne_a_quinze_minutes`.
- **Cross-origin writes are refused**, and the interactive API docs (`/docs`, `/redoc`,
  `/openapi.json`) are closed. `test_une_origine_etrangere_ne_modifie_rien`,
  `test_la_documentation_interactive_est_fermee`.
- **Accounts are isolated from each other**: an account reads and modifies nothing of
  another's, outside what is explicitly shared with it, read-only.
  `tests/integration/test_etancheite_comptes.py`, every `/api` route, generically.
- A meeting that contains an **ultra-confidential** document cannot be shared, and an
  ultra-confidential document stays invisible inside a shared project.
  `tests/integration/test_partage.py`: `test_une_reunion_ultra_confidentielle_ne_se_partage_pas`,
  `test_dans_un_projet_partage_l_ultra_confidentiel_reste_invisible`.

### Device tokens (capture companion)

- A device token (prefix `mgj_`) is shown once at creation and stored as a digest only.
  It opens **only** the companion's routes: send a recording, follow its job, list the
  account's projects. Everywhere else it gets 401, and it never returns transcript or
  report text. `tests/integration/test_jetons_appareil.py`:
  `test_un_jeton_s_affiche_une_fois_et_n_est_stocke_qu_en_empreinte`,
  `test_un_jeton_seul_recoit_401_hors_des_routes_du_compagnon`,
  `test_avec_un_jeton_le_travail_ne_rend_aucun_texte`.
- A revoked token, or a token whose account is deactivated, is worth nothing, even after
  reactivation. `test_un_jeton_retire_ne_vaut_plus_rien`,
  `test_le_jeton_d_un_compte_desactive_ne_vaut_plus_rien_meme_reactive`.
- On the Mac, the companion keeps the token in the macOS keychain only
  (`compagnon/Sources/memoglyph-compagnon/Liaison.swift`), and its messages never carry
  it (`compagnon/Tests/CaptureCoreTests/EnvoiTests.swift`).

### Network exposure

- By default, the UI, the API and the local model server bind to `127.0.0.1` only.
  `tests/unit/test_scripts.py`: `test_start_binds_ipv4_and_exposes_lan_only_on_request`.
- **LAN mode (`./start.sh --lan`)** exposes the UI, and so the sign-in screen, to your
  local network, by IP address and by `<machine>.local` name; the API stays on
  `127.0.0.1` behind the UI's proxy. Sign-in still applies, but the traffic is plain
  HTTP: on a shared network, passwords and session cookies can be read. Use it only on
  networks you trust, and stop the stack after use (`./stop.sh`).
  `test_vite_allows_local_hostnames_only_in_lan_mode`.

### Cloud providers

- The default report provider is the **local** model (MLX); a cloud provider (Mistral,
  OpenAI, Anthropic) is used only when you pick it. `tests/integration/test_api.py`:
  `test_le_mode_local_par_defaut_est_MLX`. When selected, audio and/or text is sent to
  that provider's API: choosing Mistral also switches transcription to Voxtral (the
  audio is uploaded); OpenAI and Anthropic keep transcription local (only text is
  sent). Anthropic is offered in the interface like the other providers
  (`test_anthropic_est_propose_a_l_ecran_et_un_moteur_hors_interface_ne_l_est_pas`).
- API keys live in your local `.env`, which is gitignored. Error messages strip the
  configured key and any bearer token. `tests/unit/test_secrets_dans_les_erreurs.py`.

### What the sensitivity label does, and does not, control

The per-document label (standard, confidential, ultra-confidential) is chosen by you.
By default an **ultra-confidential** document never goes to a cloud provider. One rule
(`core/sensitivity.py`, `verifier_envoi`; `cloud_refuse` is its case without
permission) is applied twice: by every route, before any read meant for the model, any
job reservation and any upload, with the same 403 message; and by the core itself, just
before calling an engine: the `llm.chat` and `llm.chat_json` facades and `pipeline.run`
(before a cloud speech engine) refuse without calling the provider.
`tests/integration/test_ultra_confidentiel.py` replaces the provider with a fake that
fails if it is called:

- **transcription**: a cloud speech engine (Mistral), or a cloud provider for the profile
  or the report, is refused before the file is received.
  `test_transcrire_un_ultra_confidentiel_en_cloud_est_refuse_avant_tout`;
- **report generation**: refused with a cloud provider.
  `test_le_cr_d_un_ultra_confidentiel_en_cloud_est_refuse`;
- **meeting facts (ECL)**: re-running it with a cloud provider is refused.
  `test_relancer_les_faits_d_un_ultra_confidentiel_en_cloud_est_refuse`;
  `tests/integration/test_ecl.py`: `test_relancer_est_reserve_au_proprietaire_et_aux_fournisseurs_proposes`;
- **assistant**: with a cloud provider, passages from ultra-confidential documents, and
  from any document whose meeting holds one, are removed before the prompt is built; the
  best allowed passages take their place, and the answer says how many were skipped. If
  nothing is left, 403. Proved end to end, from Markdown files on disk through the real
  index and search to a fake provider that captures the whole request
  (`tests/integration/test_sensibilite_recherche.py`):
  `test_en_cloud_ni_le_texte_ni_le_titre_d_un_ultra_confidentiel_ne_part`,
  `test_un_transcript_standard_d_une_reunion_ultra_confidentielle_est_ecarte`,
  `test_un_document_passe_en_ultra_confidentiel_apres_indexation_est_ecarte`,
  `test_l_ultra_confidentiel_en_tete_ne_prend_aucune_place_du_top_k`; also
  `test_l_assistant_en_cloud_n_envoie_jamais_un_passage_ultra_confidentiel`,
  `test_l_assistant_en_cloud_refuse_s_il_ne_reste_rien`;
- **search** is local (full text and the local embedding index) and calls no provider.
  `test_la_recherche_porte_la_sensibilite_et_reste_locale`;
- **sharing**: an ultra-confidential document is not shared (see above).

Every core call to the facade says which document it sends (`envoi=`), and every direct
call to an engine or cloud client outside the engine modules is listed with its reason;
both are read from the code (`tests/unit/test_envoi_par_la_facade.py`):
`test_chaque_appel_de_la_facade_dans_le_coeur_porte_envoi`,
`test_chaque_appel_direct_a_un_moteur_est_inscrit_avec_sa_raison`,
`test_en_cloud_sans_autorisation_la_facade_leve_sans_appeler_le_moteur`.

#### Allowing the cloud for your own ultra-confidential documents

An account can allow a cloud provider for **its own** ultra-confidential documents, in
**Privacy** (`GET` and `PUT /api/compte/cloud-ultra`). It is off for every account,
existing or new; turning it on asks for confirmation, turning it off does not.
`tests/integration/test_cloud_par_compte.py`, on the real chain (files on disk, real
index, real routes and background jobs, the real facade; fake engines that record each
request):

- **only the session's account**: neither an administrator for another account (an
  `identifiant` field is refused) nor a device token can read or change it.
  `test_ni_l_administrateur_ni_un_jeton_d_appareil_ne_reglent_un_autre_compte`,
  `test_un_magasin_d_avant_le_reglage_est_migre_inactif`;
- **the owner's permission, never the reader's**: the permission that counts is that of
  the account owning the document's project, read from the folder's manifest. A second
  account without the setting stays refused, on every cloud route:
  `test_transcrire_en_cloud_refuse_sans_le_reglage_puis_part_avec_et_laisse_sa_trace`,
  `test_le_profil_en_cloud_suit_le_reglage_et_le_local_ne_change_pas`,
  `test_le_cr_en_cloud_suit_le_reglage_du_proprietaire` (report and re-run report),
  `test_relancer_les_faits_en_cloud_suit_le_reglage`,
  `test_l_assistant_en_cloud_envoie_l_ultra_du_proprietaire_qui_l_autorise`,
  `test_l_assistant_decide_passage_par_passage_selon_le_proprietaire` (the assistant
  decides passage by passage), `test_le_mcp_rend_l_ultra_du_compte_seulement_avec_son_reglage`;
- **turning it off applies at once**: the setting is read again just before each send,
  including in a job started earlier.
  `test_desactiver_vaut_aussitot_pour_une_transcription_deja_lancee`,
  `test_desactiver_pendant_un_cr_arrete_l_appel_suivant`,
  `test_l_autorisation_est_celle_du_proprietaire_et_relue_a_chaque_appel`;
- **no extra right**: scope, sharing and Q16 are unchanged with the setting on. An
  ultra-confidential meeting is still not shared, and a shared project never carries
  ultra-confidential content to the guest, including a document made ultra-confidential
  after it was shared. `test_reglage_actif_le_partage_l_ultra_et_q16_sont_inchanges`,
  `test_le_mcp_d_un_lecteur_de_partage_ne_rend_jamais_l_ultra_du_proprietaire`; the
  three-account isolation test runs with the owner's setting off and on
  (`tests/integration/test_etancheite_comptes.py`);
- **trace**: each allowed send marks its usage execution (`envoi_ultra_autorise` in
  `usage.db`; the assistant now opens its own `assistant` execution), writes one log
  line per document with the account, the provider and the document identifier, never a
  title or content, and the produced transcript, report or facts file says so in its
  provenance. `test_un_envoi_autorise_marque_l_execution_et_ecrit_une_ligne_sans_contenu`,
  `test_une_base_d_usage_existante_recoit_la_colonne_a_zero`.

Local processing stays available for every level
(`test_transcrire_un_ultra_confidentiel_en_local_est_accepte`,
`test_le_cr_reste_possible_en_local_et_pour_un_document_ordinaire`). Without the setting,
the interface does not offer a cloud provider for such a document. The label is yours to
set: nothing classifies a document automatically, and changing the label or the setting
does not reprocess, or call back, what was already sent.

### Backups

A full backup covers `output/`, `index.db`, `auth.db` and `usage.db`: losing `auth.db`
loses every account and device token. See [docs/sauvegarde.md](docs/sauvegarde.md) and
`tools/sauvegarder.py`.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's
**"Report a vulnerability"** (Security Advisories) feature on this repository,
rather than opening a public issue.

Include what you can: affected version/commit, reproduction steps, and impact
as you understand it. As a small open-source project maintained on a
best-effort basis, there is no guaranteed response SLA, but reports are
genuinely welcome and will be looked at.

## Supported versions

Only the latest state of the `main` branch is supported.
