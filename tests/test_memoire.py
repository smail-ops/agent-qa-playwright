"""Mémoire des parcours : rejeu sans IA, auto-réparation quand le site change, et cas où elle ne sert pas."""

import json
import threading

from test_agent import LOGIN_OK, make_case

from agent.codegen import generate_script
from agent.execution import memory_for
from agent.memoire import Memory, fingerprint
from agent.providers import ScriptedProvider
from agent.report import build_report
from agent.runner import MEMORY_SUMMARY, STOPPED_SUMMARY, Agent
from agent.testcase import parse_test_case


class FauxModele(ScriptedProvider):
    """La mémoire sert aux vrais modèles : ce faux modèle se présente comme tel, et rejoue un scénario."""

    name, model = "gemini", "faux modèle"


def run(pw_page, demo_url, tmp_path, scenario, memory, case=None, **options):
    events = []
    agent = Agent(FauxModele(scenario), memory=memory, on_event=lambda kind, data: events.append((kind, data)), **options)
    result = agent.run(pw_page, case or make_case(demo_url), tmp_path / f"rapport-{len(list(tmp_path.iterdir()))}")
    return result, events


def edit_memory(memory, case, change):
    path = memory.path(case)
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data["steps"])
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_parcours_enregistre_apres_un_pass(pw_page, demo_url, tmp_path):
    memory = Memory(tmp_path / "memoire")
    result, _ = run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    assert result.status == "PASS" and result.memory_saved and result.model_calls == 9

    data = json.loads(memory.path(make_case(demo_url)).read_text(encoding="utf-8"))
    assert data["fingerprint"] == fingerprint(make_case(demo_url)) and data["model"] == "gemini (faux modèle)"
    assert [s["action"] for s in data["steps"]] == [s["action"] for s in LOGIN_OK[:-1]]  # sans finish ni ouverture
    assert data["steps"][0]["element"]["selector"] == "#user-name"
    assert all("ref" not in s["args"] for s in data["steps"])  # les numéros changent à chaque page : on garde l'élément
    assert memory.info(make_case(demo_url))["steps"] == 8


def test_parcours_rejoue_sans_modele(pw_page, demo_url, tmp_path):
    """Deuxième exécution : aucun appel au modèle, même résultat, mêmes tests générés."""
    memory = Memory(tmp_path / "memoire")
    first, _ = run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    second, events = run(pw_page, demo_url, tmp_path, [], memory)  # un modèle appelé conclurait BLOQUE
    assert second.status == "PASS", second.summary
    assert second.model_calls == 0 and second.memory_replayed == 8 and second.healed_at is None
    assert second.summary == MEMORY_SUMMARY
    assert [s.source for s in second.steps[1:]] == ["memoire"] * 8
    assert [kind for kind, _ in events if kind in ("memory", "heal")] == ["memory"]
    assert not any(kind == "phase" and data["phase"] == "think" for kind, data in events)  # pas de réflexion du modèle
    assert generate_script(second) == generate_script(first)
    report = build_report(second)
    assert "| Appels au modèle | 0 |" in report
    assert "| Mémoire | Parcours rejoué depuis la mémoire, sans IA (8 étapes). |" in report
    assert "| Mémoire : Étape du scénario |" in report  # chaque étape rejouée l'indique


def test_auto_reparation_quand_le_site_change(pw_page, demo_url, tmp_path):
    """Le bouton mémorisé n'existe plus : le modèle reprend à cette étape, puis la mémoire est réparée."""
    memory = Memory(tmp_path / "memoire")
    case = make_case(demo_url)
    run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)

    def break_button(steps):  # simule un site modifié : le bouton de connexion a changé de sélecteur
        steps[2]["element"].update(selector='[data-test="ancien-bouton"]', xpath='//*[@data-test="ancien-bouton"]')
    edit_memory(memory, case, break_button)

    result, events = run(pw_page, demo_url, tmp_path, LOGIN_OK[2:], memory)
    assert result.status == "PASS", result.summary
    assert result.healed_at == 4 and result.memory_replayed == 2 and result.model_calls == 7
    heal = next(data for kind, data in events if kind == "heal")
    assert heal["number"] == 4 and "introuvable" in heal["reason"]
    repaired = json.loads(memory.path(case).read_text(encoding="utf-8"))
    assert repaired["steps"][2]["element"]["selector"] == '[data-test="login-button"]'  # parcours réparé enregistré


def test_verification_echouee_au_rejeu(pw_page, demo_url, tmp_path):
    """Le chemin fonctionne mais le résultat a changé : c'est un FAIL, et la mémoire n'est pas modifiée."""
    memory = Memory(tmp_path / "memoire")
    case = make_case(demo_url)
    run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    edit_memory(memory, case, lambda steps: steps[-1]["args"].update(text="Panier (5)"))
    before = memory.path(case).read_text(encoding="utf-8")

    result, events = run(pw_page, demo_url, tmp_path, [], memory)
    assert result.status == "FAIL" and result.model_calls == 0
    assert not result.memory_saved and memory.path(case).read_text(encoding="utf-8") == before
    assert "heal" not in [kind for kind, _ in events]  # une vérification échouée ne déclenche pas de réparation


def test_cas_modifie_repart_de_l_ia(pw_page, demo_url, tmp_path):
    memory = Memory(tmp_path / "memoire")
    run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    modified = parse_test_case(f"# CT-01 Connexion et panier\nURL : {demo_url}/index.html\n\n## Étapes\n1. ...\n2. Une étape de plus")
    assert memory.load(modified) is None
    result, _ = run(pw_page, demo_url, tmp_path, LOGIN_OK, memory, case=modified)
    assert result.model_calls == 9 and result.memory_replayed == 0


def test_autre_site_autre_memoire(pw_page, demo_url, tmp_path):
    """Le même cas sur une autre adresse (recette, autre version) n'utilise pas le parcours de la production."""
    memory = Memory(tmp_path / "memoire")
    run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    assert memory.load(make_case(demo_url).with_url(f"{demo_url}/index.html?env=recette")) is None


def test_arret_pendant_le_rejeu(pw_page, demo_url, tmp_path):
    memory = Memory(tmp_path / "memoire")
    run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    stop = threading.Event()  # l'arrêt est demandé après la première étape rejouée
    agent = Agent(FauxModele([]), memory=memory, stop_event=stop,
                  on_event=lambda kind, data: stop.set() if kind == "step" and data["step"].number == 2 else None)
    stopped = agent.run(pw_page, make_case(demo_url), tmp_path / "arret")
    assert stopped.status == "BLOQUE" and stopped.summary == STOPPED_SUMMARY and len(stopped.steps) == 2
    assert not stopped.memory_saved


def test_memoire_illisible_ignoree(pw_page, demo_url, tmp_path):
    memory = Memory(tmp_path / "memoire")
    memory.folder.mkdir()
    memory.path(make_case(demo_url)).write_text("{ pas du json", encoding="utf-8")
    assert memory.load(make_case(demo_url)) is None
    result, _ = run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    assert result.status == "PASS" and result.model_calls == 9


def test_etape_memorisee_invalide_reparee(pw_page, demo_url, tmp_path):
    """Un fichier de mémoire modifié à la main passe par les mêmes contrôles qu'une réponse du modèle."""
    memory = Memory(tmp_path / "memoire")
    case = make_case(demo_url)
    run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    edit_memory(memory, case, lambda steps: steps[0].update(action="voler"))
    result, events = run(pw_page, demo_url, tmp_path, LOGIN_OK, memory)
    assert result.status == "PASS" and result.healed_at == 2
    assert "illisible" in next(data for kind, data in events if kind == "heal")["reason"]


def test_pas_de_memoire_pour_la_demo_sans_ia(tmp_path):
    assert memory_for(ScriptedProvider([]), tmp_path) is None


def test_avis_visuel_jamais_memorise(pw_page, demo_url, tmp_path):
    """Un avis visuel de l'IA ne se rejoue pas sans IA : ce parcours reste fait par le modèle."""
    memory = Memory(tmp_path / "memoire")
    scenario = [{"action": "check_text", "args": {"text": "Boutique"}},
                {"action": "check_visual", "args": {"expected": "Le formulaire est centré", "seen": "Il est centré", "ok": True}},
                {"action": "finish", "args": {"status": "PASS", "summary": "Page conforme."}}]
    result = Agent(FauxModele(scenario), memory=memory, vision=True).run(pw_page, make_case(demo_url), tmp_path / "rapport")
    assert result.status == "PASS" and not result.memory_saved
    assert memory.load(make_case(demo_url)) is None
