"""Le contrat de module : ce qu'un fichier déclare, et comment on le trouve.

Aucun vrai import ici : `charger` reçoit sa fonction d'import, donc les modules
d'essai sont des objets en mémoire. C'est ce qui permet d'éprouver le cas d'un
module qui refuse de se charger sans avoir à écrire un fichier cassé sur le
disque.
"""

import importlib
import sys
from types import SimpleNamespace

import pytest

from src.modules import Module, Publication, charger, noms_de_modules


def _module(nom: str, **surcharges) -> Module:
    """Un module minimal, pour n'écrire dans chaque test que ce qu'il éprouve."""
    defauts = {
        "titre": nom.capitalize(),
        "description": f"Le module {nom}.",
    }
    return Module(nom=nom, **{**defauts, **surcharges})


def _importeur(**modules):
    """Une fonction d'import qui rend les modules donnés, et lève pour les autres."""

    def importer(chemin: str):
        nom = chemin.rsplit(".", 1)[-1]
        if nom not in modules:
            raise ModuleNotFoundError(chemin)
        valeur = modules[nom]
        if isinstance(valeur, Exception):
            raise valeur
        return valeur

    return importer


# --- Le chargement ---------------------------------------------------------


def test_charge_les_modules_trouves():
    charges, refuses = charger(
        ["promos"], _importeur(promos=SimpleNamespace(MODULE=_module("promos")))
    )

    assert [m.nom for m in charges] == ["promos"]
    assert refuses == {}


def test_l_ordre_declare_passe_avant_le_nom_du_fichier():
    """Le menu Discord suit cet ordre : il ne doit pas dépendre de l'alphabet.

    « conversion » est la calculatrice, qu'on veut en tête ; « promos » vient
    après alors que p suit c — sans `ordre`, on ne pourrait pas l'exprimer.
    """
    charges, _ = charger(
        ["promos", "conversion"],
        _importeur(
            promos=SimpleNamespace(MODULE=_module("promos", ordre=20)),
            conversion=SimpleNamespace(MODULE=_module("conversion", ordre=10)),
        ),
    )

    assert [m.nom for m in charges] == ["conversion", "promos"]


def test_a_ordre_egal_le_nom_tranche():
    """Deux modules sans ordre explicite gardent un rang stable.

    Sinon le menu changerait d'un démarrage à l'autre selon l'ordre du système
    de fichiers, et personne ne retrouverait ses commandes au même endroit.
    """
    charges, _ = charger(
        ["zeta", "alpha"],
        _importeur(
            zeta=SimpleNamespace(MODULE=_module("zeta")),
            alpha=SimpleNamespace(MODULE=_module("alpha")),
        ),
    )

    assert [m.nom for m in charges] == ["alpha", "zeta"]


# --- Un module cassé ne casse pas le bot -----------------------------------


def test_un_module_qui_leve_a_l_import_est_ecarte_et_nomme():
    """Un module en cours d'écriture ne doit pas couper les publications.

    La raison est retenue, pas seulement le fait : « filiales n'a pas chargé »
    sans le pourquoi obligerait à relire les journaux du serveur.
    """
    charges, refuses = charger(
        ["filiales", "promos"],
        _importeur(
            filiales=ImportError("pas de module nommé pandas"),
            promos=SimpleNamespace(MODULE=_module("promos")),
        ),
    )

    assert [m.nom for m in charges] == ["promos"]
    assert "filiales" in refuses
    assert "pandas" in refuses["filiales"]


def test_un_module_sans_declaration_est_ecarte():
    """Un fichier posé dans le dossier sans `MODULE` est un oubli, pas un module.

    Écarté avec une raison qui dit quoi ajouter : le silence enverrait chercher
    la panne du côté du bot.
    """
    charges, refuses = charger(
        ["brouillon"], _importeur(brouillon=SimpleNamespace())
    )

    assert charges == []
    assert "MODULE" in refuses["brouillon"]


def test_un_module_casse_n_empeche_pas_les_suivants():
    charges, refuses = charger(
        ["a", "casse", "z"],
        _importeur(
            a=SimpleNamespace(MODULE=_module("a", ordre=1)),
            casse=RuntimeError("boum"),
            z=SimpleNamespace(MODULE=_module("z", ordre=3)),
        ),
    )

    assert [m.nom for m in charges] == ["a", "z"]
    assert list(refuses) == ["casse"]


def test_deux_modules_de_meme_nom_le_second_est_refuse():
    """Deux modules de même nom partageraient leur tiroir de réglages.

    L'un lirait l'heure de l'autre, et éteindre le premier éteindrait les deux.
    Le second est écarté en le disant, plutôt que d'écraser le premier.
    """
    charges, refuses = charger(
        ["promos", "promos-bis"],
        _importeur(
            **{
                "promos": SimpleNamespace(MODULE=_module("promos", ordre=1)),
                "promos-bis": SimpleNamespace(MODULE=_module("promos", ordre=2)),
            }
        ),
    )

    assert [m.nom for m in charges] == ["promos"]
    assert "promos" in refuses["promos-bis"]


# --- Le nom d'un module est aussi une clé ----------------------------------


@pytest.mark.parametrize("nom", ["", " ", "Promos", "mes promos", "promos!", "1promos"])
def test_un_nom_inutilisable_est_refuse(nom):
    """Le nom sert de clé de rangement et de choix dans Discord.

    Une majuscule ou un espace passerait à l'écriture et échouerait à la
    relecture, ou serait refusé par Discord au moment de synchroniser les
    commandes — c'est-à-dire au démarrage, en production.
    """
    with pytest.raises(ValueError):
        _module(nom)


@pytest.mark.parametrize("nom", ["promos", "tableau-des-frais", "module2"])
def test_un_nom_utilisable_passe(nom):
    assert _module(nom).nom == nom


def test_un_titre_vide_est_refuse():
    """Le titre est ce que `/reglages modules liste` affiche : sans lui, une ligne vide."""
    with pytest.raises(ValueError):
        Module(nom="promos", titre="", description="Des promos.")


# --- Les publications d'un module ------------------------------------------


def test_un_module_peut_ne_rien_publier():
    """La calculatrice n'a pas de post quotidien : zéro publication est normal."""
    assert _module("conversion").publications == ()


def test_un_module_peut_declarer_plusieurs_publications():
    """Rien ne plafonne le nombre de posts : un module peut en porter deux.

    C'est le cas qu'on veut voir marcher sans y revenir — un récapitulatif le
    matin et une alerte le soir, dans un seul fichier.
    """
    matin = Publication(cle="bonjour", titre="le bonjour", preparer=None)
    soir = Publication(cle="bonsoir", titre="le bonsoir", preparer=None)

    module = _module("politesse", publications=(matin, soir))

    assert [p.cle for p in module.publications] == ["bonjour", "bonsoir"]


def test_deux_publications_de_meme_cle_sont_refusees():
    """La clé range l'heure, les salons et la trace de « déjà envoyé ».

    Deux publications qui la partagent : la seconde ne partirait jamais, la
    première ayant déjà marqué la journée.
    """
    with pytest.raises(ValueError):
        _module(
            "politesse",
            publications=(
                Publication(cle="bonjour", titre="le matin", preparer=None),
                Publication(cle="bonjour", titre="le soir", preparer=None),
            ),
        )


@pytest.mark.parametrize("cle", ["", "Bonjour", "mon jour"])
def test_une_cle_de_publication_inutilisable_est_refusee(cle):
    with pytest.raises(ValueError):
        Publication(cle=cle, titre="le bonjour", preparer=None)


@pytest.mark.parametrize("heure", ["9h", "25:00", "09:60", "9:00", ""])
def test_une_heure_par_defaut_mal_ecrite_est_refusee(heure):
    """L'heure illisible ne lèverait pas : le planning retomberait sur 09:00.

    Le module publierait alors à une heure qu'il n'a pas demandée, sans que rien
    ne le dise — la panne la plus longue à trouver. Refusée à la déclaration,
    elle se voit au démarrage.
    """
    with pytest.raises(ValueError):
        Publication(
            cle="bonjour", titre="le bonjour", preparer=None, heure_par_defaut=heure
        )


@pytest.mark.parametrize("heure", ["00:00", "09:00", "23:59"])
def test_une_heure_par_defaut_bien_ecrite_passe(heure):
    publication = Publication(
        cle="bonjour", titre="le bonjour", preparer=None, heure_par_defaut=heure
    )

    assert publication.heure_par_defaut == heure


async def _rien(*_arguments):
    return None


@pytest.mark.parametrize(
    "moitie",
    [
        {"lire_heure": _rien},
        {"ecrire_heure": _rien},
        {"lire_derniere": _rien},
        {"marquer": _rien},
        {"lire_salons": _rien},
        {"lire_salons": _rien, "ajouter_salon": _rien},
        {"retirer_salon": _rien},
    ],
)
def test_lire_ici_et_ecrire_ailleurs_est_refuse(moitie):
    """Le tiroir générique étant le défaut de chaque accès *séparément*, un module
    peut sans le vouloir lire son heure dans la config et l'écrire dans le tiroir.
    La commande répondrait « ✅ », l'heure ne changerait pas, et ça ne se verrait
    que le lendemain à l'heure du post.
    """
    with pytest.raises(ValueError) as refus:
        Publication(cle="bonjour", titre="le bonjour", preparer=None, **moitie)

    # Le message nomme ce qui manque : « paires incohérentes » obligerait à
    # relire le contrat pour savoir quoi ajouter.
    assert "il manque" in str(refus.value).lower()


@pytest.mark.parametrize(
    "paires",
    [
        {},
        {"lire_heure": _rien, "ecrire_heure": _rien},
        {"lire_derniere": _rien, "marquer": _rien},
        {"lire_salons": _rien, "ajouter_salon": _rien, "retirer_salon": _rien},
    ],
)
def test_des_paires_completes_passent(paires):
    """Y compris aucune : tout laisser au tiroir générique est le cas ordinaire."""
    assert Publication(
        cle="bonjour", titre="le bonjour", preparer=None, **paires
    ).cle == "bonjour"


# --- Le balayage du dossier ------------------------------------------------


@pytest.fixture
def paquet_factice(tmp_path, monkeypatch):
    """Un vrai paquet sur le disque, pour éprouver le balayage plutôt que le simuler.

    Le balayage est la promesse du système : poser un fichier suffit. Un test qui
    ne toucherait pas au disque ne dirait rien de cette promesse.
    """
    dossier = tmp_path / "modules_dessai"
    dossier.mkdir()
    (dossier / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    # `import_module` garde ce qu'il a déjà importé : sans cette purge, un test
    # verrait le dossier temporaire du précédent. Le bot, lui, n'a qu'un paquet
    # et qu'un démarrage, donc le cache y est un avantage.
    monkeypatch.delitem(sys.modules, "modules_dessai", raising=False)
    importlib.invalidate_caches()
    return dossier


def test_le_dossier_reel_est_balaye(paquet_factice):
    """Une liste écrite en dur oublierait le fichier ajouté après elle."""
    (paquet_factice / "promos.py").write_text("", encoding="utf-8")
    (paquet_factice / "filiales.py").write_text("", encoding="utf-8")

    assert noms_de_modules("modules_dessai") == ["filiales", "promos"]


def test_les_fichiers_prefixes_par_un_blanc_souligne_sont_ignores(paquet_factice):
    """`__init__` et les fichiers de travail ne sont pas des modules.

    Sans cette règle, le paquet lui-même se chargerait comme un module et
    apparaîtrait refusé à chaque démarrage.
    """
    (paquet_factice / "promos.py").write_text("", encoding="utf-8")
    (paquet_factice / "_brouillon.py").write_text("", encoding="utf-8")

    assert noms_de_modules("modules_dessai") == ["promos"]


def test_un_dossier_vide_ne_rend_rien(paquet_factice):
    """Aucun module n'est un état valide, pas une panne : le bot démarre quand même."""
    assert noms_de_modules("modules_dessai") == []
