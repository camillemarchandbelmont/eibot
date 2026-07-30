"""Tests de la règle d'accès, partagée par Discord et l'API web.

La règle vit dans `src/acces.py`, sans dépendance à Discord ni à HTTP : les deux
façades (commandes slash et site web) l'appellent, donc `/config acces ajouter`
ouvre aussi le site. Deux implémentations divergeraient tôt ou tard.
"""

import pytest

from src.acces import acces_autorise, gere_la_liste


# --- La règle elle-même -----------------------------------------------------

def test_administrateur_autorise():
    assert acces_autorise(est_admin=True, membre_id="1", autorises=[]) is True


def test_membre_lambda_refuse():
    assert acces_autorise(est_admin=False, membre_id="1", autorises=[]) is False


def test_membre_liste_autorise():
    assert acces_autorise(est_admin=False, membre_id="42", autorises=["42"]) is True


def test_membre_hors_liste_refuse():
    assert acces_autorise(est_admin=False, membre_id="7", autorises=["42"]) is False


def test_id_numerique_accepte():
    """Discord donne des `int`, JSONB les rend tels quels : la comparaison doit
    tenir sans dépendre du type."""
    assert acces_autorise(est_admin=False, membre_id=42, autorises=["42"]) is True
    assert acces_autorise(est_admin=False, membre_id="42", autorises=[42]) is True


def test_admin_passe_meme_absent_de_la_liste():
    """Un administrateur ne peut pas se verrouiller dehors."""
    assert acces_autorise(est_admin=True, membre_id="99", autorises=["42"]) is True


def test_id_vide_refuse():
    """Un id manquant (session web incomplète) ne doit pas passer par accident.

    Le cas dangereux est `autorises=[""]` : sans garde, la chaîne vide se
    trouverait elle-même dans la liste et ouvrirait l'accès. Une entrée vide y
    arrive par une config éditée à la main ou une écriture partielle.
    """
    assert acces_autorise(est_admin=False, membre_id="", autorises=[""]) is False
    assert acces_autorise(est_admin=False, membre_id=None, autorises=[""]) is False
    assert acces_autorise(est_admin=False, membre_id=0, autorises=["0"]) is False
    assert acces_autorise(est_admin=False, membre_id=None, autorises=[]) is False


# --- Gérer la liste reste réservé aux administrateurs -----------------------

def test_gerer_la_liste_reserve_aux_admins():
    """Sinon un membre autorisé s'ajouterait des complices, ou retirerait celui
    qui l'a nommé — vrai sur le site comme dans Discord."""
    assert gere_la_liste(est_admin=True) is True
    assert gere_la_liste(est_admin=False) is False


# --- La façade Discord utilise bien cette règle ------------------------------

async def test_le_tree_delegue_a_la_regle(tmp_path, monkeypatch):
    """`ArbreProtege` ne doit pas réimplémenter la décision.

    Ce test échoue si le tree se remet à comparer les permissions lui-même :
    on neutralise la règle partagée et plus personne ne doit passer.
    """
    from src import acces
    from src.bot import EmpireBot
    from src.db import Store
    from src.source import CsvFileSource

    chemin = tmp_path / "export.csv"
    chemin.write_text(
        "# nom: Empire Immo - M8\n"
        "# mise_a_jour: 2026-07-29 12:00:07\n"
        "type,nom,niveau,valeur,loyer,charge,impot,promotion,"
        "construction,embellissement,reparation\n"
        'zones,"Technopôle",0,2710572934559948,0,0,0,17,0,0,0\n',
        encoding="utf-8",
    )
    store = Store(dsn="")
    await store.connect()
    bot = EmpireBot(store, CsvFileSource(chemin))

    class Reponse:
        def __init__(self):
            self.messages = []

        async def send_message(self, contenu=None, **options):
            self.messages.append(contenu)

    class Interaction:
        def __init__(self):
            self.user = type("U", (), {"id": 1, "guild_permissions": type(
                "P", (), {"administrator": True})()})()
            self.response = Reponse()

    monkeypatch.setattr(acces, "acces_autorise", lambda **_: False)
    monkeypatch.setattr("src.bot.acces_autorise", lambda **_: False)

    assert await bot.tree.autorisation(Interaction()) is False
