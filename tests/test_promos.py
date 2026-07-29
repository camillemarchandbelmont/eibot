"""Tests du parsing CSV et de la sélection des promotions."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.promos import Building, find_promos, parse_csv

CSV_REEL = Path(__file__).resolve().parent.parent / "buildings_batiments_entreprise.csv"

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


# --- Le vrai fichier du jeu -------------------------------------------------

@pytest.fixture(scope="module")
def reel():
    return parse_csv(CSV_REEL.read_text(encoding="utf-8"))


def test_csv_reel_116_batiments(reel):
    _, batiments = reel
    assert len(batiments) == 116
    assert all(isinstance(b, Building) for b in batiments)


def test_csv_reel_quatre_promos(reel):
    _, batiments = reel
    promos = find_promos(batiments, Decimal(0), Decimal("1e30"))
    assert [p.building.nom for p in promos] == [
        "Mégapôle millenium désaffecté",
        "Technopôle millenium désaffecté",
        "Zone portuaire désaffectée",
        "Entrepôt inexploitable",
    ]


def test_csv_reel_fourchette_par_defaut_100T_6P(reel):
    """La fourchette du jeu : 100 TØ -> 6 PØ. Un seul bâtiment y tombe."""
    _, batiments = reel
    promos = find_promos(batiments, Decimal("1e14"), Decimal("6e15"), minimum=0)
    assert [p.building.nom for p in promos] == ["Technopôle millenium désaffecté"]


def test_csv_reel_petit_budget(reel):
    _, batiments = reel
    promos = find_promos(batiments, Decimal(0), Decimal("1e6"), minimum=0)
    assert [p.building.nom for p in promos] == ["Entrepôt inexploitable"]


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


def test_csv_reel_fourchette_100T_6P_repeche_une_seconde(reel):
    """Seul le Technopole est dans 100T-6P : on complete avec le plus proche."""
    _, batiments = reel
    promos = find_promos(batiments, Decimal("1e14"), Decimal("6e15"))
    assert len(promos) == 2
    assert promos[0].building.nom == "Technopôle millenium désaffecté"
    assert promos[0].dans_fourchette is True
    # Zone portuaire (124,47 GØ) est plus proche de 100 TØ que le Mégapôle
    # (173,02 EØ) ne l'est de 6 PØ.
    assert promos[1].building.nom == "Zone portuaire désaffectée"
    assert promos[1].dans_fourchette is False
