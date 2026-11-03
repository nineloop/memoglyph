from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def test_start_binds_ipv4_and_exposes_lan_only_on_request() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "start.sh").read_text(encoding="utf-8")
    vite = (root / "ui" / "vite.config.ts").read_text(encoding="utf-8")

    help_result = subprocess.run(
        ["bash", str(root / "start.sh"), "--help"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert help_result.returncode == 0
    assert "--lan" in help_result.stdout
    assert "réseau de confiance uniquement" in help_result.stdout
    assert 'UI_HOST="${MEMOGLYPH_UI_HOST:-127.0.0.1}"' in script
    assert '--lan) UI_HOST="0.0.0.0"' in script
    assert 'MEMOGLYPH_UI_HOST="$UI_HOST"' in script
    assert "env('UI_HOST')" in vite
    assert "host: UI_HOST" in vite


def test_vite_allows_local_hostnames_only_in_lan_mode() -> None:
    """Le contrôle d'hôte de Vite 8 renvoie 403 pour un nom non-IP absent de
    `allowedHosts`. En LAN, un testeur atteint souvent ce Mac par son nom
    Bonjour `macbook.local` (plus stable que l'IP) : on doit donc autoriser les
    noms `.local` — mais UNIQUEMENT en mode LAN, jamais dans le défaut localhost.
    """
    root = Path(__file__).resolve().parents[2]
    vite = (root / "ui" / "vite.config.ts").read_text(encoding="utf-8")
    start = (root / "start.sh").read_text(encoding="utf-8")

    # LAN = 0.0.0.0 ; l'ouverture d'hôtes est conditionnée à ce seul cas.
    assert "IS_LAN = UI_HOST === '0.0.0.0'" in vite
    assert "IS_LAN ? ['.local'" in vite
    # Échappatoire pour un nom DNS d'entreprise, sans toucher au défaut.
    assert "env('ALLOWED_HOSTS')" in vite
    # Hors LAN : aucun `allowedHosts` posé → comportement Vite par défaut.
    assert "allowedHosts ? { allowedHosts } : {}" in vite

    # Le lanceur annonce l'URL Bonjour cohérente avec ce qui est autorisé.
    assert ".local:${UI_PORT}" in start
    assert "scutil --get LocalHostName" in start


def test_stop_never_claims_success_while_ports_are_still_listening(
    tmp_path: Path,
) -> None:
    """Reproduit un refus de signal sans tuer de vrai processus.

    `lsof` affirme qu'un listener résiste et `xargs` simule l'échec des kills.
    L'ancien script avalait ces erreurs, supprimait les PID et sortait avec 0.
    """
    root = Path(__file__).resolve().parents[2]
    script = tmp_path / "stop.sh"
    script.write_bytes((root / "stop.sh").read_bytes())
    script.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_lsof = fake_bin / "lsof"
    fake_lsof.write_text("#!/bin/sh\necho 424242\nexit 0\n", encoding="utf-8")
    fake_lsof.chmod(0o755)
    fake_xargs = fake_bin / "xargs"
    fake_xargs.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_xargs.chmod(0o755)

    env = dict(os.environ)
    env.update({
        "PATH": f"{fake_bin}:{env['PATH']}",
        "MEMOGLYPH_UI_PORT": "9670",
        "MEMOGLYPH_API_PORT": "9671",
        "MEMOGLYPH_PORT_BASE": "9970",
    })
    result = subprocess.run(
        ["bash", str(script)], cwd=tmp_path, env=env,
        text=True, capture_output=True, timeout=10,
    )

    assert result.returncode == 1
    assert "écoute encore" in result.stdout
    assert "Arrêt incomplet" in result.stdout
    assert "✅ Memoglyph arrêté" not in result.stdout


def test_restart_script_exists_and_invokes_stop_and_start() -> None:
    root = Path(__file__).resolve().parents[2]
    restart_script = (root / "restart.sh").read_text(encoding="utf-8")
    assert "./stop.sh" in restart_script
    assert "exec ./start.sh" in restart_script



def test_stop_retrouve_les_fichiers_de_ports_et_de_pid_de_start() -> None:
    """`stop.sh` lit les fichiers de ports et de PID que `start.sh` écrit."""
    root = Path(__file__).resolve().parents[2]
    stop = (root / "stop.sh").read_text(encoding="utf-8")
    start = (root / "start.sh").read_text(encoding="utf-8")
    assert ".memoglyph-ports" in stop
    assert '".memoglyph-api.pid"' in stop
    assert '".memoglyph-ui.pid"' in stop
    assert "> .memoglyph-ports" in start


# ---------- Arrêt : ne viser que les serveurs, et le serveur du LLM local ----------

ROOT = Path(__file__).resolve().parents[2]


def _bin(tmp_path: Path, nom: str, corps: str) -> None:
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    f = d / nom
    f.write_text("#!/bin/sh\n" + corps, encoding="utf-8")
    f.chmod(0o755)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("MEMOGLYPH_")}
    env.update({
        "PATH": f"{tmp_path / 'bin'}:{env['PATH']}",
        "MEMOGLYPH_UI_PORT": "9670", "MEMOGLYPH_API_PORT": "9671",
        "MEMOGLYPH_PORT_BASE": "9970", "MEMOGLYPH_MLX_PORT": "9772",
    })
    env.update(extra)
    return env


def _lancer(tmp_path: Path, script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    copie = tmp_path / script
    copie.write_bytes((ROOT / script).read_bytes())
    copie.chmod(0o755)
    return subprocess.run(["bash", str(copie)], cwd=tmp_path, env=env,
                          text=True, capture_output=True, timeout=60)


def test_stop_ne_tue_que_les_serveurs_qui_ecoutent(tmp_path: Path) -> None:
    """`lsof -ti:PORT` rend aussi les clients connectés (un onglet ouvert sur
    l'interface) : le processus réseau du navigateur recevait `kill -9`.

    Le faux `lsof` rend le serveur 4242 avec `-sTCP:LISTEN`, et en plus le
    client 9999 sans ce filtre. Le faux `xargs` note ce qu'on lui fait tuer.
    """
    journal = tmp_path / "tues.txt"
    _bin(tmp_path, "lsof", 'case "$*" in *-sTCP:LISTEN*) echo 4242;; *) echo 4242; echo 9999;; esac\n')
    _bin(tmp_path, "xargs", f'cat >> "{journal}"\n')
    _lancer(tmp_path, "stop.sh", _env(tmp_path))

    tues = journal.read_text(encoding="utf-8").split()
    # Ancre : le script a bien tenté d'arrêter le serveur.
    assert "4242" in tues
    assert "9999" not in tues, "un client connecté a été visé"


def _demarrage(pid: int) -> str:
    return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                          capture_output=True, text=True).stdout.strip()


def _temoin_llm(tmp_path: Path, pid: int, demarrage: str) -> Path:
    import json
    temoin = tmp_path / ".cache" / "mlx-server-9772.owner"
    temoin.parent.mkdir(parents=True, exist_ok=True)
    temoin.write_text(json.dumps({"pid": pid, "demarrage": demarrage, "port": 9772,
                                  "model": "test"}), encoding="utf-8")
    return temoin


@pytest.fixture
def serveur_factice():
    proc = subprocess.Popen(["sleep", "60"])
    yield proc
    proc.kill()
    proc.wait()


def test_stop_arrete_le_serveur_llm_dont_la_propriete_est_prouvee(
    tmp_path: Path, serveur_factice,
) -> None:
    """Le serveur `mlx_lm` survivait à `./stop.sh` : l'API ne l'arrête pas en
    s'éteignant. PID et heure de démarrage concordent : il est arrêté."""
    _bin(tmp_path, "lsof", "exit 1\n")
    temoin = _temoin_llm(tmp_path, serveur_factice.pid, _demarrage(serveur_factice.pid))
    result = _lancer(tmp_path, "stop.sh", _env(tmp_path))

    assert serveur_factice.wait(timeout=15) is not None
    assert not temoin.exists()
    assert "Serveur du LLM local arrêté" in result.stdout
    assert result.returncode == 0, result.stdout


def test_stop_ne_touche_pas_un_pid_recycle(tmp_path: Path, serveur_factice) -> None:
    """Le PID du témoin existe, mais son heure de démarrage n'est pas celle du
    serveur lancé par Memoglyph : c'est un autre processus, laissé en vie."""
    _bin(tmp_path, "lsof", "exit 1\n")
    temoin = _temoin_llm(tmp_path, serveur_factice.pid, "Mon Jan  1 00:00:00 2001")
    result = _lancer(tmp_path, "stop.sh", _env(tmp_path))

    assert serveur_factice.poll() is None, "un processus non prouvé a été tué"
    assert temoin.exists()
    assert "laissé tel quel" in result.stdout


def test_stop_signale_un_serveur_llm_sans_temoin_et_le_laisse(tmp_path: Path) -> None:
    """Sans témoin, la propriété n'est pas prouvée : rien n'est tué, et le
    script le dit au lieu de se taire."""
    journal = tmp_path / "tues.txt"
    _bin(tmp_path, "lsof", 'case "$*" in *9772*-sTCP:LISTEN*|*-sTCP:LISTEN*9772*) echo 5555;; *) exit 1;; esac\n')
    _bin(tmp_path, "xargs", f'cat >> "{journal}"\n')
    result = _lancer(tmp_path, "stop.sh", _env(tmp_path))

    assert "sans témoin de propriété : laissé tel quel" in result.stdout
    assert not journal.exists() or "5555" not in journal.read_text(encoding="utf-8")


def test_stop_retire_un_temoin_llm_perime(tmp_path: Path) -> None:
    mort = subprocess.Popen(["sleep", "60"])
    mort.kill()
    mort.wait()
    _bin(tmp_path, "lsof", "exit 1\n")
    temoin = _temoin_llm(tmp_path, mort.pid, "Mon Jan  1 00:00:00 2001")
    result = _lancer(tmp_path, "stop.sh", _env(tmp_path))

    assert not temoin.exists()
    assert result.returncode == 0, result.stdout


# ---------- Démarrage : une pile déjà en marche garde ses ports ----------

def test_un_second_start_reprend_les_ports_de_la_pile_en_marche(
    tmp_path: Path, serveur_factice,
) -> None:
    """Un second `./start.sh` croyait les ports pris par autre chose, décalait
    sur la paire suivante, réécrivait `.memoglyph-ports` et attendait 40 s des
    serveurs absents, pour finir en succès.

    Hermétique : le contrôle de dépendances de `start.sh` interroge `$PYTHON`,
    remplacé ici par un faux interpréteur qui répond oui. Les dépendances de
    la machine (absentes en CI) ne décident pas du résultat, et aucun vrai
    serveur n'est lancé : les deux PID enregistrés sont vivants.
    """
    for nom in (".memoglyph-api.pid", ".memoglyph-ui.pid"):
        (tmp_path / nom).write_text(str(serveur_factice.pid), encoding="utf-8")
    ports = tmp_path / ".memoglyph-ports"
    ports.write_text("UI_PORT=9680\nAPI_PORT=9681\n", encoding="utf-8")
    os.utime(ports, ns=(1, 1))
    (tmp_path / "ui").mkdir()
    _bin(tmp_path, "lsof", "echo 4242\n")       # tous les ports écoutent
    _bin(tmp_path, "curl", "exit 0\n")          # les deux serveurs répondent
    _bin(tmp_path, "ffmpeg", "exit 0\n")
    _bin(tmp_path, "python-factice", "exit 0\n")
    env = _env(tmp_path, PYTHON=str(tmp_path / "bin" / "python-factice"))
    for k in ("MEMOGLYPH_UI_PORT", "MEMOGLYPH_API_PORT"):
        env.pop(k)

    result = _lancer(tmp_path, "start.sh", env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ses ports sont repris" in result.stdout
    assert result.stdout.count("tourne déjà (PID") == 2
    assert "http://127.0.0.1:9680" in result.stdout
    # Le fichier de ports n'est pas réécrit, pas même à l'identique.
    assert ports.stat().st_mtime_ns == 1


def test_start_sort_en_echec_sur_delai_depasse() -> None:
    import re

    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert re.search(r"Délai dépassé[^\n]*\n\s*exit 1", start)


def test_start_lance_l_api_sans_tampon_de_sortie() -> None:
    """Redirigé vers `memoglyph-api.log`, Python écrit par blocs : le journal
    retardait de plusieurs minutes, et une erreur récente n'y figurait pas."""
    import re

    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert re.search(r"env PYTHONUNBUFFERED=1 \"\$PYTHON\" -m uvicorn api\.main:app", start)


def _pile_en_marche(tmp_path: Path, serveur) -> dict[str, str]:
    for nom in (".memoglyph-api.pid", ".memoglyph-ui.pid"):
        (tmp_path / nom).write_text(str(serveur.pid), encoding="utf-8")
    (tmp_path / ".memoglyph-ports").write_text("UI_PORT=9680\nAPI_PORT=9681\n", encoding="utf-8")
    (tmp_path / "ui").mkdir()
    _bin(tmp_path, "lsof", "echo 4242\n")
    _bin(tmp_path, "curl", "exit 0\n")
    _bin(tmp_path, "ffmpeg", "exit 0\n")
    _bin(tmp_path, "python-factice", "exit 0\n")
    env = _env(tmp_path, PYTHON=str(tmp_path / "bin" / "python-factice"))
    for k in ("MEMOGLYPH_UI_PORT", "MEMOGLYPH_API_PORT"):
        env.pop(k)
    return env


def test_start_affiche_le_code_d_installation_au_premier_lancement(
    tmp_path: Path, serveur_factice,
) -> None:
    """Le code n'est jamais journalisé : c'est le terminal de `./start.sh` qui le montre."""
    env = _pile_en_marche(tmp_path, serveur_factice)
    (tmp_path / ".memoglyph-installation").write_text("ABCD-EFGH-JKMN\n", encoding="utf-8")

    result = _lancer(tmp_path, "start.sh", env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Créer le compte administrateur" in result.stdout
    assert "ABCD-EFGH-JKMN" in result.stdout


def test_start_ne_montre_aucun_code_quand_un_compte_existe(
    tmp_path: Path, serveur_factice,
) -> None:
    env = _pile_en_marche(tmp_path, serveur_factice)

    result = _lancer(tmp_path, "start.sh", env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Code d'installation" not in result.stdout


def test_start_n_affiche_plus_la_documentation_de_l_api() -> None:
    """`/docs` est fermé (auth 2) : le lien ne mènerait qu'à une 404."""
    assert "/docs" not in (ROOT / "start.sh").read_text(encoding="utf-8")


# ---------- Démarrage : attendre une API qui réindexe, échouer sur une API morte ----------

def _pile_a_lancer(tmp_path: Path, python: str, curl: str, **extra: str) -> dict[str, str]:
    """Une racine sans pile en marche : `start.sh` lance de faux serveurs.

    Le faux interpréteur répond oui au contrôle de dépendances (`-c`), puis
    joue l'API ; le faux `npm` joue Vite et vit 30 s.
    """
    (tmp_path / "ui").mkdir()
    _bin(tmp_path, "lsof", "exit 1\n")          # aucun port pris
    _bin(tmp_path, "ffmpeg", "exit 0\n")
    _bin(tmp_path, "npm", "exec sleep 30\n")
    _bin(tmp_path, "curl", curl)
    _bin(tmp_path, "python-factice", 'case "$1" in -c) exit 0;; esac\n' + python)
    env = _env(tmp_path, PYTHON=str(tmp_path / "bin" / "python-factice"), **extra)
    return env


def _arreter_les_factices(tmp_path: Path) -> None:
    import signal
    for nom in (".memoglyph-api.pid", ".memoglyph-ui.pid"):
        f = tmp_path / nom
        if f.exists():
            try:
                os.kill(int(f.read_text().strip()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


def test_start_attend_une_api_vivante_au_dela_des_premieres_secondes(tmp_path: Path) -> None:
    """L'API ne répond qu'après sa réindexation : `start.sh` attend tant
    qu'elle vit. Le faux `curl` ne voit la santé qu'à son quatrième appel."""
    compteur = tmp_path / "appels"
    curl = (f'case "$*" in *health*) n=$(cat "{compteur}" 2>/dev/null || echo 0); '
            f'n=$((n + 1)); echo $n > "{compteur}"; [ $n -ge 4 ];; *) exit 0;; esac\n')
    env = _pile_a_lancer(tmp_path, "exec sleep 30\n", curl)
    try:
        result = _lancer(tmp_path, "start.sh", env)
    finally:
        _arreter_les_factices(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Memoglyph prêt en" in result.stdout
    assert int(compteur.read_text()) == 4


def test_start_signale_tout_de_suite_une_api_morte_avec_son_journal(tmp_path: Path) -> None:
    import time

    env = _pile_a_lancer(tmp_path, 'echo "Traceback : index illisible"\nexit 1\n', "exit 7\n")
    debut = time.monotonic()
    try:
        result = _lancer(tmp_path, "start.sh", env)
    finally:
        _arreter_les_factices(tmp_path)

    assert result.returncode == 1
    assert "L'API s'est arrêté pendant son démarrage" in result.stdout
    assert "Traceback : index illisible" in result.stdout
    assert "Délai dépassé" not in result.stdout
    assert time.monotonic() - debut < 15


def test_start_garde_un_plafond_reglable(tmp_path: Path) -> None:
    env = _pile_a_lancer(tmp_path, "exec sleep 30\n", "exit 7\n", MEMOGLYPH_ATTENTE_S="2")
    try:
        result = _lancer(tmp_path, "start.sh", env)
    finally:
        _arreter_les_factices(tmp_path)

    assert result.returncode == 1
    assert "Délai dépassé (2 s, réglable par MEMOGLYPH_ATTENTE_S)" in result.stdout


def test_le_plafond_par_defaut_laisse_le_temps_d_une_reindexation() -> None:
    import re

    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    defaut = re.search(r'ATTENTE_S="\$\{MEMOGLYPH_ATTENTE_S:-(\d+)\}"', start)
    assert defaut and int(defaut.group(1)) >= 600


# ---------- Arrêt : ne pas couper un traitement en cours ----------

def _releve(tmp_path: Path, pid: int, nature: str = "transcription") -> Path:
    import json
    f = tmp_path / ".memoglyph-traitements.json"
    f.write_text(json.dumps({"pid": pid, "traitements": [
        {"id": "a1", "nature": nature, "statut": "running", "progression": 0.42,
         "depuis": "10:31"}]}), encoding="utf-8")
    return f


def test_stop_refuse_un_traitement_en_cours_puis_force(tmp_path: Path, serveur_factice) -> None:
    _bin(tmp_path, "lsof", "exit 1\n")
    (tmp_path / ".memoglyph-api.pid").write_text(str(serveur_factice.pid), encoding="utf-8")
    releve = _releve(tmp_path, serveur_factice.pid)

    refus = _lancer(tmp_path, "stop.sh", _env(tmp_path))
    assert refus.returncode == 3, refus.stdout
    assert "transcription : 42 %, depuis 10:31" in refus.stdout
    assert "./stop.sh --forcer" in refus.stdout
    assert "Arrêt refusé" in refus.stdout
    assert serveur_factice.poll() is None, "l'API a été arrêtée malgré le refus"
    assert (tmp_path / ".memoglyph-api.pid").exists() and releve.exists()

    copie = tmp_path / "stop.sh"
    force = subprocess.run(["bash", str(copie), "--forcer"], cwd=tmp_path, env=_env(tmp_path),
                           text=True, capture_output=True, timeout=60)
    assert force.returncode == 0, force.stdout
    assert "les traitements en cours sont perdus" in force.stdout
    assert serveur_factice.wait(timeout=15) is not None
    assert not releve.exists()


def test_un_releve_perime_ne_bloque_jamais_l_arret(tmp_path: Path) -> None:
    mort = subprocess.Popen(["sleep", "60"])
    mort.kill()
    mort.wait()
    _bin(tmp_path, "lsof", "exit 1\n")
    releve = _releve(tmp_path, mort.pid)

    result = _lancer(tmp_path, "stop.sh", _env(tmp_path))

    assert result.returncode == 0, result.stdout
    assert "périmé" in result.stdout
    assert not releve.exists()


def test_un_releve_dont_le_pid_n_est_plus_l_api_est_perime(tmp_path: Path, serveur_factice) -> None:
    """PID recyclé : vivant, mais ni l'API du fichier PID ni `uvicorn api.main`."""
    _bin(tmp_path, "lsof", "exit 1\n")
    releve = _releve(tmp_path, serveur_factice.pid)

    result = _lancer(tmp_path, "stop.sh", _env(tmp_path))

    assert result.returncode == 0, result.stdout
    assert "n'est plus l'API" in result.stdout
    assert serveur_factice.poll() is None
    assert not releve.exists()


def test_restart_transmet_forcer_a_stop_et_le_reste_a_start(tmp_path: Path) -> None:
    journal = tmp_path / "appels.txt"
    for nom in ("stop.sh", "start.sh"):
        f = tmp_path / nom
        f.write_text(f'#!/bin/sh\necho "{nom} $*" >> "{journal}"\n', encoding="utf-8")
        f.chmod(0o755)
    copie = tmp_path / "restart.sh"
    copie.write_bytes((ROOT / "restart.sh").read_bytes())

    for options in ([], ["--lan", "--forcer"]):
        subprocess.run(["bash", str(copie), *options], cwd=tmp_path, check=True, timeout=10)

    assert journal.read_text(encoding="utf-8").splitlines() == [
        "stop.sh ", "start.sh ", "stop.sh --forcer", "start.sh --lan"]


# ---------- Arrêt : le balayage des orphelins ne sort pas de sa racine ----------

@pytest.fixture
def api_factice():
    """Un processus que `ps` montre comme une API Memoglyph."""
    proc = subprocess.Popen(["bash", "-c", 'exec -a "python -m uvicorn api.main:app" sleep 60'])
    yield proc
    proc.kill()
    proc.wait()


def _balayage(tmp_path: Path, pid: int, dossier: Path) -> subprocess.CompletedProcess:
    """Le faux `lsof` montre `pid` en écoute sur 9980, lancé depuis `dossier`."""
    _bin(tmp_path, "lsof", (
        f'case "$*" in\n'
        f'  *-d\\ cwd*) printf "p{pid}\\nfcwd\\nn{dossier}\\n";;\n'
        f'  *-ti:9980\\ -sTCP:LISTEN*) echo {pid};;\n'
        f'  *) exit 1;;\n'
        f'esac\n'))
    return _lancer(tmp_path, "stop.sh", _env(tmp_path))


def test_le_balayage_epargne_l_api_d_une_autre_racine(tmp_path: Path, api_factice) -> None:
    autre = tmp_path.parent / "autre-racine"
    result = _balayage(tmp_path, api_factice.pid, autre)

    assert api_factice.poll() is None, "l'API d'une autre racine a été tuée"
    assert "d'une autre racine" in result.stdout
    assert str(autre) in result.stdout
    assert result.returncode == 0, result.stdout


def test_le_balayage_arrete_un_orphelin_de_sa_racine(tmp_path: Path, api_factice) -> None:
    result = _balayage(tmp_path, api_factice.pid, tmp_path.resolve() / "ui")

    assert api_factice.wait(timeout=15) is not None
    assert "orpheline nettoyée sur le port 9980" in result.stdout


def test_le_releve_de_stop_garde_son_format_malgre_les_champs_ajoutes_au_job(tmp_path: Path) -> None:
    """`stop.sh` lit `pid` et `traitements[id, nature, statut, progression, depuis]` : le chantier 003
    ajoute des champs au `Job` (titre, projet, jeton…) qui ne doivent JAMAIS entrer dans ce fichier."""
    import json

    from api.jobs import JobManager

    fichier = tmp_path / ".memoglyph-traitements.json"
    manager = JobManager(releve=fichier)
    job = manager.create(owner="alice", nature="transcription", titre="TITRE PRIVÉ", projet_nom="PROJET",
                         jeton="JETON")
    contenu = json.loads(fichier.read_text(encoding="utf-8"))
    assert set(contenu) == {"pid", "traitements"}
    assert contenu["traitements"] == [{
        "id": job.id, "nature": "transcription", "statut": "pending", "progression": 0.0,
        "depuis": job.depuis}]
    assert "TITRE" not in fichier.read_text(encoding="utf-8") and "JETON" not in fichier.read_text(encoding="utf-8")
    assert len(job.depuis) == 5 and job.depuis[2] == ":"          # « HH:MM », comme avant
