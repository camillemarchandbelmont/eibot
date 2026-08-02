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
        "heure": "09:00",
        "fuseau": "Europe/Paris",
        "role_id": "123",
        "logs_salon_id": None,
    }
    fourchettes = [
        {"nom": "grosses", "prix_min": "1e14", "prix_max": "6e15",
         "salons": ["111", "222"]},
    ]
    rendu = config_en_json(config, fourchettes=fourchettes)
    fourchette = rendu["fourchettes"][0]

    assert fourchette["nom"] == "grosses"
    assert fourchette["salons"] == ["111", "222"]
    assert fourchette["prix_min_brut"] == "100000000000000"   # 1e14 développé
    assert fourchette["prix_min"] == "100,00 TØ"
    assert rendu["heure"] == "09:00"
    assert rendu["roles"] == {}
    assert "role_id" not in rendu
    assert rendu["logs_salon_id"] is None
    assert json.dumps(rendu)   # sérialisable


def test_config_en_json_sans_fourchette():
    """Un bot neuf : liste vide, et surtout pas de fourchette inventée."""
    rendu = config_en_json(
        {"heure": "09:00", "fuseau": "Europe/Paris"}, fourchettes=[]
    )
    assert rendu["fourchettes"] == []


def test_config_en_json_ne_garde_pas_les_prix_a_la_racine():
    """Les laisser ferait afficher au site une fourchette qui n'existe plus.

    Le champ resterait alimenté par les défauts d'usine : plausible et faux,
    le pire des deux mondes.
    """
    rendu = config_en_json(
        {"heure": "09:00", "fuseau": "Europe/Paris",
         "prix_min": "1e14", "prix_max": "6e15"},
        fourchettes=[],
    )
    for champ in ("prix_min", "prix_max", "prix_min_brut", "prix_max_brut"):
        assert champ not in rendu, champ


def test_config_sans_mention_ni_journal():
    rendu = config_en_json(
        {"heure": "09:00", "fuseau": "Europe/Paris"}, fourchettes=[]
    )
    assert rendu["roles"] == {}
    assert rendu["fourchettes"] == []


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
            "heure": "09:00", "fuseau": "Europe/Paris", "role_id": "42",
            "logs_salon_id": "7", "autorises": ["1"],
        },
        fourchettes=[
            {"nom": "grosses", "prix_min": "100000", "prix_max": "1e14",
             "salons": ["123"]},
        ],
    )

    for champ in ("heure", "fuseau"):
        assert isinstance(rendu[champ], str), champ
    assert isinstance(rendu["autorises"], list)
    # `str | None` côté site : ni 0, ni "", ni False.
    assert rendu["logs_salon_id"] is None or isinstance(rendu["logs_salon_id"], str)

    # `roles`, `serveurs` et `salons_connus` sont des dicts dont clés et valeurs
    # sont des chaînes. `role_global` est `str | None`.
    for champ in ("roles", "serveurs"):
        assert isinstance(rendu[champ], dict), champ
        for cle, valeur in rendu[champ].items():
            assert isinstance(cle, str), f"{champ}: clé {cle}"
            assert isinstance(valeur, str), f"{champ}: valeur {valeur}"
    assert rendu["role_global"] is None or isinstance(rendu["role_global"], str)
    assert isinstance(rendu["salons_connus"], dict)
    for cle, valeur in rendu["salons_connus"].items():
        assert isinstance(cle, str), f"salons_connus: clé {cle}"
        assert isinstance(valeur, dict), f"salons_connus: valeur {valeur}"
        assert isinstance(valeur.get("nom"), str), f"salons_connus[{cle}].nom"
        assert isinstance(valeur.get("serveur"), str), f"salons_connus[{cle}].serveur"

    # Une fourchette porte son nom, ses salons et ses deux bornes en trois
    # formes : c'est ce que `Fourchette` déclare dans `D:\eiweb\lib\fourchettes.ts`.
    assert isinstance(rendu["fourchettes"], list)
    fourchette = rendu["fourchettes"][0]
    assert isinstance(fourchette["nom"], str)
    assert isinstance(fourchette["salons"], list)
    for nom in ("prix_min", "prix_max"):
        for suffixe in ("", "_long", "_brut"):
            champ = f"{nom}{suffixe}"
            assert isinstance(fourchette[champ], str), champ


def test_contrat_roles_par_serveur():
    """Le site doit pouvoir dire quel serveur mentionne quoi.

    Un `role_id` unique laisserait croire que les deux serveurs sont pingués
    alors qu'un seul l'est.
    """
    rendu = config_en_json(
        {"heure": "09:00", "fuseau": "Europe/Paris", "roles": {"111": "42"}},
        [],
    )

    assert rendu["roles"] == {"111": "42"}
    # `role_id` ne doit plus exister : le laisser inviterait le site à
    # l'afficher, donc à mentir dès qu'il y a deux serveurs.
    assert "role_id" not in rendu


def test_contrat_un_role_vide_est_masque_et_non_affiche_vide():
    """Un serveur sans rôle ne doit pas apparaître dans `roles`.

    JSONB restitue tel quel ce qu'on y a mis : une entrée `{"222": null}` (écriture
    partielle, réglage à moitié effacé) traverserait la sérialisation. Le site
    afficherait « Second serveur : <@&None> », donc un rôle qui n'existe pas, au
    lieu de dire simplement que ce serveur ne pingue personne.
    """
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "roles": {"111": "42", "222": None, "333": ""},
        },
        [],
    )

    assert rendu["roles"] == {"111": "42"}


def test_contrat_roles_absents_donnent_un_dict_vide():
    """Et non `null` : le site itère dessus sans garde."""
    rendu = config_en_json({"heure": "09:00", "fuseau": "Europe/Paris"}, [])

    assert rendu["roles"] == {}
    assert rendu["serveurs"] == {}
    assert rendu["salons_connus"] == {}


def test_contrat_role_id_plat_devient_un_role_par_serveur_connu():
    """Compatibilité d'affichage : une config d'avant ne doit pas afficher
    « aucune mention » alors que le bot pingue bien."""
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "role_id": "7",
            "serveurs": {"111": "Empire Immo"},
        },
        [],
    )

    # Après la correction : le rôle global est exposé explicitement, et
    # `roles` ne contient plus d'étalement. Le site affiche désormais le
    # rôle global comme tel, au lieu de le dupliquer par serveur.
    assert rendu["roles"] == {}
    assert rendu["role_global"] == "7"


def test_contrat_role_global_expose_quand_aucun_serveur_connu():
    """Le jour du déploiement : un role_id existe, mais aucun serveur connu.

    Sans `role_global`, le site afficherait « aucune mention » alors que le
    bot pingue bien. Le rôle global est donc exposé explicitement.
    """
    rendu = config_en_json(
        {"heure": "09:00", "fuseau": "Europe/Paris", "role_id": "7", "salons": ["1"]},
        [],
    )

    assert rendu["roles"] == {}
    assert rendu["role_global"] == "7"


def test_contrat_role_global_absent_quand_roles_par_serveur():
    """Une fois la table `roles` remplie, `role_global` est `None`."""
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "roles": {"111": "42"},
        },
        [],
    )

    assert rendu["roles"] == {"111": "42"}
    assert rendu["role_global"] is None


def test_contrat_role_global_absent_quand_rien_regle():
    """Ni role_id, ni roles : aucune mention."""
    rendu = config_en_json(
        {"heure": "09:00", "fuseau": "Europe/Paris"},
        [],
    )

    assert rendu["roles"] == {}
    assert rendu["role_global"] is None


def test_contrat_role_global_coerce_en_texte():
    """JSONB peut restituer un int : le site compare des chaînes."""
    rendu = config_en_json(
        {"heure": "09:00", "fuseau": "Europe/Paris", "role_id": 42},
        [],
    )

    assert rendu["role_global"] == "42"


def test_contrat_role_global_absent_quand_table_existe():
    """Une fois la table `roles` remplie, même si `role_id` subsiste (migration
    partielle), c'est la table qui fait foi : `role_global` doit être `None`."""
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "role_id": "7",
            "roles": {"111": "42"},
        },
        [],
    )

    assert rendu["roles"] == {"111": "42"}
    assert rendu["role_global"] is None


def test_contrat_salons_connus():
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "serveurs": {"111": "Empire Immo"},
            "salons_connus": {"1": {"nom": "promos", "serveur": "111"}},
        },
        [],
    )

    assert rendu["serveurs"] == {"111": "Empire Immo"}
    assert rendu["salons_connus"]["1"]["nom"] == "promos"
    assert rendu["salons_connus"]["1"]["serveur"] == "111"


def test_contrat_ids_toujours_en_texte():
    """JSONB peut restituer un int : le site compare des chaînes, et `111 !=
    "111"` en TypeScript ferait échouer le groupement sans erreur."""
    rendu = config_en_json(
        {
            "heure": "09:00",
            "fuseau": "Europe/Paris",
            "roles": {111: 42},
            "serveurs": {111: "Empire Immo"},
            "salons_connus": {1: {"nom": "promos", "serveur": 111}},
        },
        [],
    )

    assert rendu["roles"] == {"111": "42"}
    assert rendu["serveurs"] == {"111": "Empire Immo"}
    assert rendu["salons_connus"]["1"]["serveur"] == "111"


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
