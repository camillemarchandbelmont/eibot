"""Tests de la substitution des placeholders dans un export Discohook."""

import re
from decimal import Decimal

import pytest

from src.promos import Meta, find_promos, parse_csv
from src.template import (
    PLACEHOLDERS,
    _chaines,
    TEMPLATE_DEFAUT,
    TemplateError,
    champs_promo,
    placeholders_inconnus,
    rendre,
    valider_template,
)

CSV = """# nom: Empire Immo - M8
# taux_promoteur: 73
# mise_a_jour: 2026-07-28 08:00:07
type,nom,niveau,valeur,loyer,charge,impot,promotion,construction,embellissement,reparation
industriels,"Entrepôt inexploitable",0,302620,0,0,283,17,611961,87354,62063
"""


@pytest.fixture
def promo():
    meta, batiments = parse_csv(CSV)
    return meta, find_promos(batiments, Decimal(0), Decimal("1e12"))[0]


# --- Champs disponibles -----------------------------------------------------

def test_montants_en_notation_du_jeu(promo):
    meta, p = promo
    champs = champs_promo(p, meta, "2026-07-28")
    assert champs["prix"] == "302.62 KØ"
    assert champs["nom"] == "Entrepôt inexploitable"
    assert champs["remise"] == "17 %"


def test_variantes_long_et_brut(promo):
    meta, p = promo
    champs = champs_promo(p, meta, "2026-07-28")
    assert champs["prix_long"] == "302 620 Ø"
    assert champs["prix_brut"] == "302620"


def test_champs_de_meta(promo):
    meta, p = promo
    champs = champs_promo(p, meta, "2026-07-28")
    assert champs["monde"] == "Empire Immo - M8"
    assert champs["taux_promoteur"] == "73"
    assert champs["mise_a_jour"] == "2026-07-28 08:00:07"
    assert champs["date"] == "2026-07-28"


def test_tous_les_placeholders_documentes_sont_produits(promo):
    meta, p = promo
    champs = champs_promo(p, meta, "2026-07-28")
    manquants = set(PLACEHOLDERS) - set(champs)
    assert not manquants, f"placeholders documentés mais absents : {manquants}"


# --- Substitution récursive -------------------------------------------------

def test_substitution_dans_toutes_les_chaines(promo):
    meta, p = promo
    modele = {
        "content": "Promo : {nom}",
        "embeds": [
            {
                "title": "🏢 {nom}",
                "description": "Prix {prix}",
                "color": 3066993,
                "fields": [{"name": "Économie", "value": "{economie}", "inline": True}],
                "footer": {"text": "{rang}/{total} • {monde}"},
                "author": {"name": "{type}"},
            }
        ],
    }
    rendu = rendre(modele, champs_promo(p, meta, "2026-07-28"))
    embed = rendu["embeds"][0]
    assert rendu["content"] == "Promo : Entrepôt inexploitable"
    assert embed["title"] == "🏢 Entrepôt inexploitable"
    assert embed["description"] == "Prix 302.62 KØ"
    assert embed["fields"][0]["value"] == "61.98 KØ"
    assert embed["footer"]["text"] == "1/1 • Empire Immo - M8"
    assert embed["author"]["name"] == "industriels"
    # Les non-chaînes sont préservées telles quelles.
    assert embed["color"] == 3066993
    assert embed["fields"][0]["inline"] is True


def test_placeholder_inconnu_laisse_intact(promo):
    meta, p = promo
    rendu = rendre({"content": "{nom} {inexistant}"}, champs_promo(p, meta, "2026-07-28"))
    assert rendu["content"] == "Entrepôt inexploitable {inexistant}"


def test_accolades_litterales_preservees(promo):
    meta, p = promo
    rendu = rendre({"content": "JSON {} vide"}, champs_promo(p, meta, "2026-07-28"))
    assert rendu["content"] == "JSON {} vide"


def test_detection_des_placeholders_inconnus():
    modele = {"embeds": [{"title": "{nom}", "description": "{prixx} {truc}"}]}
    assert placeholders_inconnus(modele) == {"prixx", "truc"}


def test_aucun_inconnu_dans_le_template_par_defaut():
    assert placeholders_inconnus(TEMPLATE_DEFAUT) == set()


# --- Validation -------------------------------------------------------------

def test_template_par_defaut_valide():
    valider_template(TEMPLATE_DEFAUT)


def test_refuse_ce_qui_nest_pas_un_objet():
    with pytest.raises(TemplateError):
        valider_template([1, 2, 3])


def test_refuse_sans_embed_ni_contenu():
    with pytest.raises(TemplateError):
        valider_template({"username": "bot"})


def test_refuse_plusieurs_embeds():
    """Le template décrit UN bâtiment ; le bot en génère un par promo."""
    with pytest.raises(TemplateError) as exc:
        valider_template({"embeds": [{"title": "a"}, {"title": "b"}]})
    assert "un seul" in str(exc.value).lower()


def test_accepte_contenu_seul():
    valider_template({"content": "{nom} à {prix}"})


def test_rendu_du_template_par_defaut(promo):
    meta, p = promo
    rendu = rendre(TEMPLATE_DEFAUT, champs_promo(p, meta, "2026-07-28"))
    embed = rendu["embeds"][0]

    assert embed["title"].endswith("Entrepôt inexploitable")
    assert embed["fields"][0]["value"] == "**302.62 KØ**"
    assert embed["footer"]["text"].startswith("1/1")

    # Aucun placeholder ne doit subsister dans le rendu final.
    restants = set()
    for chaine in _chaines(rendu):
        restants.update(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", chaine))
    assert restants == set()


# --- Promos repêchées : aucun marquage --------------------------------------
# Le repêchage complète un post trop pauvre, mais ne se signale plus : ni
# `{hors_fourchette}`, ni `{dans_fourchette}`. `{ecart}` reste disponible pour
# qui veut afficher la distance à la fourchette.

@pytest.fixture
def repechee():
    """Un CSV où la seule promo est hors de la fourchette demandée."""
    meta, batiments = parse_csv(CSV)
    return meta, find_promos(batiments, Decimal("1e9"), Decimal("1e12"))[0]


def test_aucun_champ_ne_signale_le_repechage(repechee):
    """Les marqueurs existent encore (compatibilité) mais rendent du vide."""
    meta, p = repechee
    champs = champs_promo(p, meta, "2026-07-28")
    assert champs["hors_fourchette"] == ""
    assert champs["dans_fourchette"] == ""
    assert not any("hors fourchette" in v.lower() for v in champs.values())


def test_ecart_reste_disponible(repechee):
    """Utile pour un template qui veut afficher la distance, sans avertir."""
    meta, p = repechee
    champs = champs_promo(p, meta, "2026-07-28")
    # 1 GØ - 302.62 KØ. ` ` : l'espace avant le symbole est insécable.
    assert champs["ecart"] == "999.70 MØ"
    assert champs["ecart_brut"] == "999697380"


def test_ecart_nul_dans_la_fourchette(promo):
    meta, p = promo
    assert champs_promo(p, meta, "2026-07-28")["ecart"] == "0 Ø"


def test_marqueurs_retires_des_placeholders():
    """Sinon `/template champs` proposerait un placeholder qui ne rend rien."""
    assert "hors_fourchette" not in PLACEHOLDERS
    assert "dans_fourchette" not in PLACEHOLDERS


def test_template_par_defaut_ne_signale_rien(repechee):
    meta, p = repechee
    rendu = rendre(TEMPLATE_DEFAUT, champs_promo(p, meta, "2026-07-28"))
    assert not any("hors fourchette" in c.lower() for c in _chaines(rendu))
    assert "⚠️" not in "".join(_chaines(rendu))


def test_ancien_marqueur_rendu_vide(repechee):
    """Un template chargé avant le retrait contient `{hors_fourchette}`.

    Le laisser inconnu l'afficherait littéralement dans le post — pire que
    l'avertissement qu'on vient d'enlever. Il rend donc une chaîne vide.
    """
    meta, p = repechee
    rendu = rendre(
        {"content": "{nom}{hors_fourchette}{dans_fourchette}"},
        champs_promo(p, meta, "2026-07-28"),
    )
    assert rendu["content"] == "Entrepôt inexploitable"


def test_ancien_marqueur_pas_signale_comme_faute():
    """Sinon `/template charger` crierait à la faute de frappe sur un template
    parfaitement valide jusqu'ici."""
    assert placeholders_inconnus({"content": "{hors_fourchette}{dans_fourchette}"}) == set()


# --- Template par défaut : contenu de l'embed -------------------------------

def test_template_par_defaut_tient_sur_une_ligne_de_champs(promo):
    """Trois champs : prix, avant remise, économie.

    Embellissement / Réparation / Loyer net formaient une seconde ligne dont tu
    ne veux pas. Les placeholders restent disponibles pour un template
    personnalisé — c'est seulement le défaut qui ne les affiche plus.
    """
    meta, p = promo
    rendu = rendre(TEMPLATE_DEFAUT, champs_promo(p, meta, "2026-07-28"))
    champs = rendu["embeds"][0]["fields"]

    assert [c["name"] for c in champs] == ["Prix promo", "Avant remise", "Économie"]


def test_les_placeholders_retires_restent_utilisables(promo):
    meta, p = promo
    rendu = rendre(
        {"content": "{embellissement} {reparation} {loyer_net}"},
        champs_promo(p, meta, "2026-07-28"),
    )
    assert "{" not in rendu["content"]
