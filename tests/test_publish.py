"""Tests de l'assemblage des embeds (sans appel réseau)."""

from decimal import Decimal

import pytest

from src.promos import find_promos, parse_csv
from src.publish import construire_embeds, grouper_messages, message_aucune_promo
from src.template import TEMPLATE_DEFAUT

CSV = """# nom: Empire Immo - M8
# mise_a_jour: 2026-07-28 08:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
industriels,"Entrepôt",0,302620,0,0,283,17,611961,87354,62063
zones,"Zone portuaire",0,124467906332,0,0,555863558,17,170372929440,85767684651,49281650331
"""


@pytest.fixture
def donnees():
    meta, batiments = parse_csv(CSV)
    return meta, find_promos(batiments, Decimal(0), Decimal("1e30"))


def test_un_embed_par_promo(donnees):
    meta, promos = donnees
    embeds, _ = construire_embeds(promos, meta, TEMPLATE_DEFAUT, "2026-07-28")
    assert len(embeds) == 2


def test_ordre_conserve_du_plus_cher_au_moins_cher(donnees):
    meta, promos = donnees
    embeds, _ = construire_embeds(promos, meta, TEMPLATE_DEFAUT, "2026-07-28")
    assert "Zone portuaire" in embeds[0]["title"]
    assert "Entrepôt" in embeds[1]["title"]


def test_contenu_pris_une_seule_fois(donnees):
    meta, promos = donnees
    modele = {"content": "Promos du jour", "embeds": [{"title": "{nom}"}]}
    _, contenu = construire_embeds(promos, meta, modele, "2026-07-28")
    assert contenu == "Promos du jour"


def test_cles_inconnues_retirees(donnees):
    meta, promos = donnees
    modele = {"embeds": [{"title": "{nom}", "truc_invente": "x"}]}
    embeds, _ = construire_embeds(promos, meta, modele, "2026-07-28")
    assert "truc_invente" not in embeds[0]


def test_titre_tronque_a_256(donnees):
    meta, promos = donnees
    modele = {"embeds": [{"title": "A" * 400}]}
    embeds, _ = construire_embeds(promos, meta, modele, "2026-07-28")
    assert len(embeds[0]["title"]) == 256


def test_champs_limites_a_25(donnees):
    meta, promos = donnees
    modele = {
        "embeds": [
            {"fields": [{"name": f"n{i}", "value": "v"} for i in range(40)]}
        ]
    }
    embeds, _ = construire_embeds(promos, meta, modele, "2026-07-28")
    assert len(embeds[0]["fields"]) == 25


def test_groupage_dix_embeds_max():
    embeds = [{"title": f"t{i}"} for i in range(23)]
    paquets = grouper_messages(embeds)
    assert [len(p) for p in paquets] == [10, 10, 3]


def test_groupage_respecte_6000_caracteres():
    # Chaque embed pèse ~2500 caractères : deux par message maximum.
    embeds = [{"title": "T", "description": "D" * 2500} for _ in range(5)]
    paquets = grouper_messages(embeds)
    assert all(sum(len(e["description"]) for e in p) <= 6000 for p in paquets)
    assert len(paquets) == 3


def test_groupage_liste_vide():
    assert grouper_messages([]) == []


def test_embed_unique_trop_gros_reste_seul():
    """Un embed dépassant à lui seul le quota ne doit pas bloquer l'envoi."""
    paquets = grouper_messages([{"description": "D" * 7000}])
    assert len(paquets) == 1


def test_message_aucune_promo_affiche_la_fourchette(donnees):
    meta, _ = donnees
    texte = message_aucune_promo(Decimal("1e14"), Decimal("6e15"), meta)
    assert "100.00 TØ" in texte
    assert "6.00 PØ" in texte
    assert "Empire Immo - M8" in texte


# --- Promos repêchées : aucun marquage --------------------------------------

def test_le_post_ne_signale_pas_les_promos_repechees():
    """Une promo repêchée apparaît comme les autres, sans avertissement.

    Ni note sous le message, ni mention dans l'embed : c'était deux fois le
    même signal, et tu ne veux ni l'un ni l'autre.
    """
    meta, batiments = parse_csv(CSV)
    promos = find_promos(batiments, Decimal(0), Decimal("1e6"))
    assert any(not promo.dans_fourchette for promo in promos), "fixture inutile"

    embeds, contenu = construire_embeds(promos, meta, TEMPLATE_DEFAUT, "2026-07-28")

    rendu = (str(embeds) + contenu).lower()
    assert "hors fourchette" not in rendu
    assert "trop peu de promotions" not in rendu
