"""Tests du parsing CSV et de la sélection des promotions."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.promos import Building, find_promos, parse_csv

#: L'export du dépôt, celui que le bot lit vraiment. Il est **remplacé** à
#: chaque nouvel export du jeu : n'en attendre que le format, jamais des noms de
#: bâtiments ni des montants précis.
CSV_VIVANT = Path(__file__).resolve().parent.parent / "buildings_batiments_entreprise.csv"

#: Un export figé (mise à jour du 2026-07-28, 4 promotions à −17 %). C'est lui
#: qui sert aux tests de sélection : leurs assertions citent des noms et des
#: prix, qui n'ont de sens que sur des données immuables.
CSV_FIGE = Path(__file__).resolve().parent / "fixtures" / "export_2026-07-28.csv"

ENTETE = """# nom: Empire Immo - M8
# description: Liste des bâtiments du monde 8
# taux_promoteur: 73
# mise_a_jour: 2026-07-28 08:00:07
# -----------------------------
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
"""


def _csv(*lignes: str) -> str:
    return ENTETE + "\n".join(lignes) + "\n"


# --- En-tête commentée -------------------------------------------------------

def test_parse_meta_depuis_les_lignes_commentees():
    meta, _ = parse_csv(_csv('bureaux,"Local",0,100,0,0,0,0,0,0,0'))
    assert meta.monde == "Empire Immo - M8"
    assert meta.taux_promoteur == "73"
    assert meta.mise_a_jour == "2026-07-28 08:00:07"


def test_lignes_commentees_ne_sont_pas_des_batiments():
    _, batiments = parse_csv(_csv('bureaux,"Local",0,100,0,0,0,0,0,0,0'))
    assert len(batiments) == 1


# --- Nombres extrêmes -------------------------------------------------------

def test_entier_au_dela_de_64_bits_sans_perte():
    grand = "138131471904669765329"
    _, batiments = parse_csv(
        _csv(f'zones,"Mégapôle",0,{grand},0,0,0,17,0,0,0')
    )
    assert batiments[0].valeur == Decimal(grand)


def test_notation_scientifique():
    _, batiments = parse_csv(
        _csv('zones,"Technopôle",2,1.2477054702985E+14,0,0,0,0,0,0,0')
    )
    assert batiments[0].valeur == Decimal("124770547029850")


def test_champ_vide_vaut_zero():
    _, batiments = parse_csv(_csv('bureaux,"Local",0,100,,,,0,,,'))
    assert batiments[0].loyer == Decimal(0)
    assert batiments[0].charge == Decimal(0)


# --- Calcul de la remise ----------------------------------------------------

def test_prix_origine_et_economie_sur_entrepot():
    """valeur = prix déjà remisé ; 302 620 à -17 % vient de ~364 602."""
    _, batiments = parse_csv(
        _csv('industriels,"Entrepôt inexploitable",0,302620,0,0,283,17,611961,87354,62063')
    )
    promo = find_promos(batiments, Decimal(0), Decimal("1e12"))[0]
    assert promo.prix == Decimal("302620")
    assert promo.prix_origine.quantize(Decimal(1)) == Decimal("364602")
    assert promo.economie.quantize(Decimal(1)) == Decimal("61982")
    assert promo.remise == Decimal("17")


def test_loyer_net_retire_charge_et_impot():
    _, batiments = parse_csv(
        _csv('bureaux,"Local",1,1000,500,100,50,10,0,0,0')
    )
    promo = find_promos(batiments, Decimal(0), Decimal("1e12"))[0]
    assert promo.loyer_net == Decimal("350")


# --- Filtre et tri ----------------------------------------------------------

def test_promotion_zero_exclue():
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Sans promo",0,500,0,0,0,0,0,0,0',
            'bureaux,"Avec promo",0,500,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(0), Decimal("1e12"))
    assert [p.building.nom for p in promos] == ["Avec promo"]


def test_bornes_incluses():
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Pile min",0,100,0,0,0,17,0,0,0',
            'bureaux,"Pile max",0,200,0,0,0,17,0,0,0',
            'bureaux,"Sous min",0,99,0,0,0,17,0,0,0',
            'bureaux,"Sur max",0,201,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(100), Decimal(200))
    assert {p.building.nom for p in promos} == {"Pile min", "Pile max"}


def test_tri_du_plus_cher_au_moins_cher():
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Petit",0,100,0,0,0,17,0,0,0',
            'bureaux,"Grand",0,900,0,0,0,17,0,0,0',
            'bureaux,"Moyen",0,500,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(0), Decimal("1e12"))
    assert [p.building.nom for p in promos] == ["Grand", "Moyen", "Petit"]


def test_rang_et_total_renseignes():
    _, batiments = parse_csv(
        _csv(
            'bureaux,"A",0,900,0,0,0,17,0,0,0',
            'bureaux,"B",0,100,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(0), Decimal("1e12"))
    assert [(p.rang, p.total) for p in promos] == [(1, 2), (2, 2)]


def test_aucun_resultat_liste_vide():
    """En filtre strict (`minimum=0`), hors fourchette = rien."""
    _, batiments = parse_csv(_csv('bureaux,"Local",0,500,0,0,0,17,0,0,0'))
    assert find_promos(batiments, Decimal("1e9"), Decimal("1e12"), minimum=0) == []


# --- Un export figé du jeu --------------------------------------------------
#
# Les tests qui citent des noms de bâtiments lisent `CSV_FIGE`, pas l'export du
# dépôt : celui-ci est remplacé à chaque nouvel export du jeu, et les promotions
# du jour changent (le 2026-07-30, quatre autres bâtiments, à −8 %). Les épingler
# sur le fichier vivant rendait ces tests rouges sans qu'aucun code n'ait bougé.

@pytest.fixture(scope="module")
def fige():
    return parse_csv(CSV_FIGE.read_text(encoding="utf-8"))


def test_csv_fige_116_batiments(fige):
    _, batiments = fige
    assert len(batiments) == 116
    assert all(isinstance(b, Building) for b in batiments)


def test_csv_fige_quatre_promos(fige):
    _, batiments = fige
    promos = find_promos(batiments, Decimal(0), Decimal("1e30"))
    assert [p.building.nom for p in promos] == [
        "Mégapôle millenium désaffecté",
        "Technopôle millenium désaffecté",
        "Zone portuaire désaffectée",
        "Entrepôt inexploitable",
    ]


def test_csv_fige_fourchette_par_defaut_100T_6P(fige):
    """La fourchette du jeu : 100 TØ -> 6 PØ. Un seul bâtiment y tombe."""
    _, batiments = fige
    promos = find_promos(batiments, Decimal("1e14"), Decimal("6e15"), minimum=0)
    assert [p.building.nom for p in promos] == ["Technopôle millenium désaffecté"]


def test_csv_fige_petit_budget(fige):
    _, batiments = fige
    promos = find_promos(batiments, Decimal(0), Decimal("1e6"), minimum=0)
    assert [p.building.nom for p in promos] == ["Entrepôt inexploitable"]


# --- L'export réellement embarqué dans le dépôt -----------------------------

def test_csv_vivant_reste_lisible():
    """Le seul test légitime sur l'export du dépôt : son **format**.

    Il attrape un remplacement par un fichier tronqué, réencodé ou aux colonnes
    renommées — c'est-à-dire ce qui ferait publier « 0 bâtiment » demain matin —
    sans rien supposer des promotions du jour.
    """
    meta, batiments = parse_csv(CSV_VIVANT.read_text(encoding="utf-8"))

    assert meta.monde, "en-tête `# nom:` absente"
    assert meta.mise_a_jour, "en-tête `# mise_a_jour:` absente"
    assert len(batiments) > 100, f"seulement {len(batiments)} bâtiments lus"
    # Les montants doivent être exploitables, pas tous nuls après un parsing raté.
    assert any(b.valeur > 0 for b in batiments)


# --- Repêchage quand la fourchette est trop pauvre --------------------------

def test_une_seule_dans_la_fourchette_on_repeche_la_plus_proche():
    """Objectif : 2 promos. La 2e vient du bord le plus proche."""
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Dedans",0,150,0,0,0,17,0,0,0',
            'bureaux,"Juste au-dessus",0,210,0,0,0,17,0,0,0',
            'bureaux,"Tres loin",0,100000,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(100), Decimal(200))
    assert [p.building.nom for p in promos] == ["Dedans", "Juste au-dessus"]
    assert promos[0].dans_fourchette is True
    assert promos[1].dans_fourchette is False


def test_aucune_dans_la_fourchette_on_repeche_les_deux_plus_proches():
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Sous de peu",0,95,0,0,0,17,0,0,0',
            'bureaux,"Sur de peu",0,205,0,0,0,17,0,0,0',
            'bureaux,"Tres loin",0,100000,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(100), Decimal(200))
    assert {p.building.nom for p in promos} == {"Sous de peu", "Sur de peu"}
    assert all(not p.dans_fourchette for p in promos)


def test_ecart_calcule_par_rapport_au_bord_le_plus_proche():
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Sous",0,90,0,0,0,17,0,0,0',
            'bureaux,"Sur",0,230,0,0,0,17,0,0,0',
        )
    )
    promos = {p.building.nom: p for p in find_promos(batiments, Decimal(100), Decimal(200))}
    assert promos["Sous"].ecart == Decimal(10)   # 100 - 90
    assert promos["Sur"].ecart == Decimal(30)    # 230 - 200


def test_ecart_nul_pour_les_promos_dans_la_fourchette():
    _, batiments = parse_csv(
        _csv(
            'bureaux,"A",0,150,0,0,0,17,0,0,0',
            'bureaux,"B",0,180,0,0,0,17,0,0,0',
        )
    )
    for promo in find_promos(batiments, Decimal(100), Decimal(200)):
        assert promo.ecart == Decimal(0)
        assert promo.dans_fourchette is True


def test_deux_dans_la_fourchette_aucun_repechage():
    """Le minimum étant atteint, on ne va pas chercher au-delà."""
    _, batiments = parse_csv(
        _csv(
            'bureaux,"A",0,150,0,0,0,17,0,0,0',
            'bureaux,"B",0,180,0,0,0,17,0,0,0',
            'bureaux,"Hors",0,500,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(100), Decimal(200))
    assert [p.building.nom for p in promos] == ["B", "A"]


def test_plus_de_deux_dans_la_fourchette_tout_est_garde():
    _, batiments = parse_csv(
        _csv(
            'bureaux,"A",0,110,0,0,0,17,0,0,0',
            'bureaux,"B",0,150,0,0,0,17,0,0,0',
            'bureaux,"C",0,190,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(100), Decimal(200))
    assert [p.building.nom for p in promos] == ["C", "B", "A"]


def test_repechage_ne_prend_que_des_promos():
    """Un bâtiment proche mais sans promotion ne doit pas être repêché."""
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Dedans",0,150,0,0,0,17,0,0,0',
            'bureaux,"Proche sans promo",0,205,0,0,0,0,0,0,0',
            'bureaux,"Loin avec promo",0,900,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(100), Decimal(200))
    assert [p.building.nom for p in promos] == ["Dedans", "Loin avec promo"]


def test_minimum_zero_desactive_le_repechage():
    _, batiments = parse_csv(_csv('bureaux,"Hors",0,900,0,0,0,17,0,0,0'))
    assert find_promos(batiments, Decimal(100), Decimal(200), minimum=0) == []


def test_pas_assez_de_promos_au_total():
    """Une seule promo existe : on ne peut pas en inventer une deuxieme."""
    _, batiments = parse_csv(_csv('bureaux,"Unique",0,900,0,0,0,17,0,0,0'))
    promos = find_promos(batiments, Decimal(100), Decimal(200))
    assert len(promos) == 1
    assert promos[0].total == 1


def test_aucune_promo_du_tout():
    _, batiments = parse_csv(_csv('bureaux,"Rien",0,150,0,0,0,0,0,0,0'))
    assert find_promos(batiments, Decimal(100), Decimal(200)) == []


# --- Le repêchage classe en proportion, pas en distance --------------------
#
# Une distance en Ø n'a pas le même sens en bas et en haut de l'échelle : sur la
# fourchette 100 TØ → 6 PØ, un bâtiment à 1 Ø est « à 100 TØ » du bord bas, donc
# aussi proche qu'un bâtiment à 6,1 PØ l'est du bord haut. Il est pourtant cent
# mille milliards de fois trop petit, alors que l'autre n'est qu'à 1,7 % au-dessus
# du budget. Le classement se fait donc sur le **facteur** qui sépare le prix du
# bord, pas sur leur différence.

def test_repechage_prefere_le_plus_proche_en_proportion():
    """1 Ø est ×100 000 000 000 000 sous le bord ; 6,101 PØ est ×1,017 au-dessus.

    En distance, les deux sont « à ~100 TØ » du bord et le tri retenait le
    minuscule — un bâtiment inutilisable — en écartant celui qui dépasse le
    budget de 1,7 %.
    """
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Minuscule",0,1,0,0,0,17,0,0,0',
            'bureaux,"Juste au-dessus",0,6101000000000000,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal("1e14"), Decimal("6e15"), minimum=1)

    assert [p.building.nom for p in promos] == ["Juste au-dessus"]


def test_repechage_symetrique_de_part_et_dautre():
    """Un même facteur d'écart des deux côtés : le plus cher passe devant.

    50 est ×2 sous 100, 400 est ×2 au-dessus de 200. À facteur égal, la
    règle existante s'applique — le plus cher d'abord.
    """
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Moitie",0,50,0,0,0,17,0,0,0',
            'bureaux,"Double",0,400,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(100), Decimal(200), minimum=2)

    assert [p.building.nom for p in promos] == ["Double", "Moitie"]


def test_repechage_un_prix_nul_passe_en_dernier():
    """Un prix de 0 rendrait le facteur infini : il ne doit ni planter ni gagner.

    Le jeu n'affiche pas de bâtiment gratuit, mais un export corrompu ou une
    colonne décalée en produirait un — et une division par zéro couperait la
    publication du matin.
    """
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Gratuit",0,0,0,0,0,17,0,0,0',
            'bureaux,"Loin mais fini",0,1000000,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(100), Decimal(200), minimum=1)

    assert [p.building.nom for p in promos] == ["Loin mais fini"]


def test_repechage_borne_basse_nulle_ne_plante_pas():
    """`/fourchette prix min:0` est accepté : 0/x doit rester calculable.

    Avec un bord bas à 0, aucun bâtiment n'est « sous » la fourchette ; seuls
    ceux qui dépassent le bord haut sont repêchés.
    """
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Un peu trop cher",0,250,0,0,0,17,0,0,0',
            'bureaux,"Beaucoup trop cher",0,20000,0,0,0,17,0,0,0',
        )
    )
    promos = find_promos(batiments, Decimal(0), Decimal(200), minimum=1)

    assert [p.building.nom for p in promos] == ["Un peu trop cher"]


def test_ecart_reste_une_distance_en_or():
    """Le tri change, pas le champ affiché.

    `{ecart}` est un montant dans le template Discohook : y mettre un facteur
    afficherait « 803 Ø » pour un ratio de ×803, un chiffre absurde.
    """
    _, batiments = parse_csv(
        _csv(
            'bureaux,"Sous",0,90,0,0,0,17,0,0,0',
            'bureaux,"Sur",0,230,0,0,0,17,0,0,0',
        )
    )
    promos = {p.building.nom: p for p in find_promos(batiments, Decimal(100), Decimal(200))}

    assert promos["Sous"].ecart == Decimal(10)
    assert promos["Sur"].ecart == Decimal(30)


def test_csv_fige_fourchette_100T_6P_repeche_une_seconde(fige):
    """Seul le Technopole est dans 100T-6P : on complete avec le plus proche."""
    _, batiments = fige
    promos = find_promos(batiments, Decimal("1e14"), Decimal("6e15"))
    assert len(promos) == 2
    assert promos[0].building.nom == "Technopôle millenium désaffecté"
    assert promos[0].dans_fourchette is True
    # Zone portuaire (124,47 GØ) est plus proche de 100 TØ que le Mégapôle
    # (173,02 EØ) ne l'est de 6 PØ.
    assert promos[1].building.nom == "Zone portuaire désaffectée"
    assert promos[1].dans_fourchette is False
