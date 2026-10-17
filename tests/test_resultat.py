"""resultat.json : le bilan d'une exécution, lisible par un programme."""

import json

from agent.providers import ScriptedProvider
from agent.resultat import read_results, summary, write_result
from agent.runner import Agent
from agent.testcase import parse_test_case


def test_bilan_ecrit_puis_relu(pw_page, demo_url, tmp_path):
    case = parse_test_case(f"# CT-07 Page d'accueil\nURL : {demo_url}/index.html\n")
    scenario = [{"action": "check_text", "args": {"text": "Boutique Démo"}},
                {"action": "check_text", "args": {"text": "Paiement validé"}},
                {"action": "finish", "args": {"status": "PASS", "summary": "Accueil affiché"}}]
    run = Agent(ScriptedProvider(scenario)).run(pw_page, case, tmp_path / "rapports" / "20261018-090500_ct_07")
    data = summary(run)
    assert data["statut"] == "FAIL" and data["verifications_reussies"] == 1
    assert data["verifications_echouees"] == ["Texte « Paiement validé » absent de la page"]
    write_result(run)
    assert json.loads((run.out_dir / "resultat.json").read_text(encoding="utf-8"))["titre"] == "CT-07 Page d'accueil"


def test_derniers_resultats_et_anciens_rapports(tmp_path):
    ancien = tmp_path / "20261017-101500_ct_01"
    ancien.mkdir()
    (ancien / "rapport.md").write_text("# Rapport : CT-01 Connexion\n\n| | |\n|---|---|\n| Statut | **✅ PASS** |\n\n"
                                       "**Conclusion :** Connexion réussie\n", encoding="utf-8")
    recent = tmp_path / "20261018-090000_ct_02"
    recent.mkdir()
    (recent / "resultat.json").write_text(json.dumps({"titre": "CT-02 Bloqué", "statut": "BLOQUE", "conclusion": "x"}),
                                          encoding="utf-8")
    (tmp_path / "pas_un_rapport").mkdir()
    results = read_results(tmp_path)
    assert [(r["titre"], r["statut"]) for r in results] == [("CT-02 Bloqué", "BLOQUE"), ("CT-01 Connexion", "PASS")]
    assert results[1]["debut"] == "2026-10-17T10:15:00" and results[1]["conclusion"] == "Connexion réussie"
    assert read_results(tmp_path, limit=1)[0]["dossier"] == "20261018-090000_ct_02"
