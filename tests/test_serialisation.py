"""Tests de la conversion des promotions en JSON.

Le piège de ce projet : les montants vont jusqu'à 21 chiffres et ne survivent
pas à un `float` (donc pas à un `number` JSON, qui est un double IEEE 754). Tout
montant traverse le JSON en **chaîne de caractères**.
"""

import json
from decimal import Decimal

import pytest

from src.promos import Building, Meta, to_promo
from src.serialisation import (
    config_en_json,
    etat_en_json,
    promo_en_json,
    promos_en_json,
)


def _batiment(valeur: str, remise: str = "17", nom: str = "Technopôle") -> Building:
    return Building(
        type="zones",
        nom=nom,
        niveau=0,
        valeur=Decimal(valeur),
        loyer=Decimal("1000"),
        charge=Decimal("300"),
        impot=Decimal("200"),
        promotion=Decimal(remise),
        construction=Decimal(0),
        embellissement=Decimal(0),
        reparation=Decimal(0),
    )


# --- Aucune perte de précision ----------------------------------------------

def test_montant_de_21_chiffres_intact():
    """`138131471904669765329` ne passe pas en float : il doit rester exact."""
    promo = to_promo(_batiment("138131471904669765329"))
    rendu = promo_en_json(promo)

    assert rendu["prix_brut"] == "138131471904669765329"
    # Et il survit à un aller-retour JSON, ce qui est le vrai test.
    assert json.loads(json.dumps(rendu))["prix_brut"] == "138131471904669765329"


def test_aucun_float_dans_le_json():
    """Un seul float suffirait à corrompre silencieusement un montant.

    On inspecte récursivement : un `float` imbriqué passerait inaperçu.
    """
    rendu = promo_en_json(to_promo(_batiment("2710572934559948")))

    def valeurs(objet):
        if isinstance(objet, dict):
            for v in objet.values():
                yield from valeurs(v)
        elif isinstance(objet, list):
            for v in objet:
                yield from valeurs(v)
        else:
            yield objet

    for valeur in valeurs(rendu):
        assert not isinstance(valeur, float), f"float trouvé : {valeur!r}"
        assert not isinstance(valeur, Decimal), f"Decimal non sérialisé : {valeur!r}"


def test_json_sans_perte_apres_aller_retour():
    """La valeur relue doit reconstruire exactement le même Decimal."""
    origine = Decimal("138131471904669765329")
    rendu = json.loads(json.dumps(promo_en_json(to_promo(_batiment(str(origine))))))
    assert Decimal(rendu["prix_brut"]) == origine


# --- Deux formes pour deux usages -------------------------------------------

def test_montant_formate_et_brut():
    """Le site affiche `prix` et trie sur `prix_brut` : un tri lexicographique
    sur « 2,71 PØ » donnerait un ordre absurde."""
    rendu = promo_en_json(to_promo(_batiment("2710572934559948")))

    assert rendu["prix"] == "2,71 PØ"      # espace insécable, comme dans Discord
    assert rendu["prix_brut"] == "2710572934559948"


def test_remise_lisible():
    rendu = promo_en_json(to_promo(_batiment("302620", remise="17")))
    assert rendu["remise"] == "17 %"
    assert rendu["remise_brut"] == "17"


def test_calculs_conformes_au_jeu():
    """L'Entrepôt du CSV : 302 620 payés à −17 % → 364 602 avant remise."""
    rendu = promo_en_json(to_promo(_batiment("302620")))

    assert rendu["prix_brut"] == "302620"
    assert rendu["prix_origine"].startswith("364,6")
    assert Decimal(rendu["economie_brut"]) > 0


def test_repechage_expose():
    """Le site peut vouloir distinguer une promo repêchée, même si le post
    Discord ne le signale plus."""
    promo = to_promo(_batiment("100"))
    promo = type(promo)(**{**promo.__dict__, "dans_fourchette": False,
                           "ecart": Decimal("500")})
    rendu = promo_en_json(promo)

    assert rendu["dans_fourchette"] is False
    assert rendu["ecart_brut"] == "500"


# --- Config et état ---------------------------------------------------------

def test_config_en_json():
    config = {
        "prix_min": "1e14",
        "prix_max": "6e15",
        "heure": "09:00",
        "fuseau": "Europe/Paris",
        "role_id": "123",
        "logs_salon_id": None,
    }
    rendu = config_en_json(config, salons=["111", "222"])

    assert rendu["prix_min_brut"] == "100000000000000"   # 1e14 développé, pas "1e14"
    assert rendu["prix_min"] == "100,00 TØ"
    assert rendu["heure"] == "09:00"
    assert rendu["salons"] == ["111", "222"]
    assert rendu["role_id"] == "123"
    assert rendu["logs_salon_id"] is None
    assert json.dumps(rendu)   # sérialisable


def test_config_sans_mention_ni_journal():
    rendu = config_en_json(
        {"prix_min": "1", "prix_max": "2", "heure": "09:00",
         "fuseau": "Europe/Paris"},
        salons=[],
    )
    assert rendu["role_id"] is None
    assert rendu["salons"] == []


def test_etat_en_json():
    rendu = etat_en_json(
        pret=True,
        source="📄 fichier local",
        derniere_publication="2026-07-30",
        persistant=True,
    )

    assert rendu["pret"] is True
    assert rendu["stockage"] == "postgres"
    assert rendu["derniere_publication"] == "2026-07-30"
    assert json.dumps(rendu)


def test_etat_dit_quand_le_stockage_est_volatile():
    """Le site doit pouvoir avertir : sans Postgres, tout réglage est perdu au
    redémarrage."""
    rendu = etat_en_json(pret=False, source="", derniere_publication=None,
                         persistant=False)
    assert rendu["stockage"] == "memoire"
    assert rendu["derniere_publication"] is None


def test_etat_ne_fuit_pas_la_cle_dapi():
    """`decrire` masque déjà la clé ; on vérifie qu'on ne la réintroduit pas."""
    rendu = etat_en_json(
        pret=True,
        source="🌐 API Empire Immo\n-# https://x/y.csv?key=***",
        derniere_publication=None, persistant=True,
    )
    assert "***" in rendu["source"]
    assert "key=***" in rendu["source"]


def test_promos_en_json_porte_lentete():
    """Le monde et la date de mise à jour accompagnent la liste, pas chaque
    promotion : les répéter grossirait la réponse sans rien apporter."""
    promos = [to_promo(_batiment("302620")), to_promo(_batiment("100"))]
    rendu = promos_en_json(
        promos, Meta(monde="Empire Immo - M8", mise_a_jour="2026-07-29 12:00:07"),
        date="2026-07-30",
    )

    assert rendu["monde"] == "Empire Immo - M8"
    assert rendu["mise_a_jour"] == "2026-07-29 12:00:07"
    assert rendu["date"] == "2026-07-30"
    assert rendu["total"] == 2
    assert len(rendu["promos"]) == 2
    assert "monde" not in rendu["promos"][0]
    assert json.dumps(rendu)


def test_promos_en_json_liste_vide():
    rendu = promos_en_json([], Meta(), date="2026-07-30")
    assert rendu["total"] == 0
    assert rendu["promos"] == []


# --- Contrat avec le site web -----------------------------------------------
#
# Ces trois tests figent les *noms de champs* que le site lit. Renommer
# `dans_fourchette` ou oublier `loyer_net_brut` ne casserait rien côté Python :
# le site afficherait simplement une colonne vide, sans erreur, et on ne le
# verrait qu'en comparant à la main avec le jeu. Ils échouent donc dès qu'un
# champ disparaît — le moment où il faut aussi corriger `lib/bot.ts`.

#: Montants attendus par le site, chacun en trois formes (`x`, `x_long`,
#: `x_brut`). Aligné sur le type `NomMontant` de `D:\eiweb\lib\bot.ts`.
MONTANTS_ATTENDUS = (
    "prix", "prix_origine", "economie", "loyer", "charge", "impot",
    "loyer_net", "construction", "embellissement", "reparation", "ecart",
)


def test_contrat_promo_champs_attendus():
    rendu = promo_en_json(to_promo(_batiment("302620")))

    for nom in MONTANTS_ATTENDUS:
        for suffixe in ("", "_long", "_brut"):
            champ = f"{nom}{suffixe}"
            assert champ in rendu, f"champ absent : {champ}"
            assert isinstance(rendu[champ], str), f"{champ} doit être une chaîne"

    for champ in ("nom", "type", "remise", "remise_brut"):
        assert isinstance(rendu[champ], str), champ
    for champ in ("niveau", "rang", "total"):
        assert isinstance(rendu[champ], int), champ
    assert isinstance(rendu["dans_fourchette"], bool)


def test_contrat_config_champs_attendus():
    rendu = config_en_json(
        {
            "prix_min": "100000", "prix_max": "1e14", "heure": "09:00",
            "fuseau": "Europe/Paris", "role_id": "42", "logs_salon_id": "7",
            "autorises": ["1"],
        },
        salons=["123"],
    )

    for champ in ("heure", "fuseau"):
        assert isinstance(rendu[champ], str), champ
    for nom in ("prix_min", "prix_max"):
        for suffixe in ("", "_long", "_brut"):
            assert isinstance(rendu[f"{nom}{suffixe}"], str), f"{nom}{suffixe}"
    for champ in ("salons", "autorises"):
        assert isinstance(rendu[champ], list), champ
    # `str | None` côté site : ni 0, ni "", ni False.
    for champ in ("role_id", "logs_salon_id"):
        assert rendu[champ] is None or isinstance(rendu[champ], str), champ


def test_contrat_etat_champs_attendus():
    rendu = etat_en_json(
        pret=True, source="📄 fichier local", derniere_publication="2026-07-30",
        persistant=False,
    )

    assert isinstance(rendu["pret"], bool)
    assert isinstance(rendu["source"], str)
    # Le site branche un bandeau d'alerte sur ces deux valeurs exactes : une
    # troisième, ou un renommage, le rendrait muet.
    assert rendu["stockage"] in ("postgres", "memoire")
    assert rendu["derniere_publication"] is None or isinstance(
        rendu["derniere_publication"], str
    )
