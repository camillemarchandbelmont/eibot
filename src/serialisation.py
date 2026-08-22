"""Conversion des objets métier en JSON, pour l'API du site web.

**Aucun montant ne traverse le JSON en `number`.** Un `number` JSON est un
double IEEE 754 : `138131471904669765329` en ressortirait à
`138131471904669800000`, et le site afficherait un prix faux sans que rien ne
signale l'erreur. Tout montant est donc une **chaîne de chiffres**, que le site
relit tel quel (ou via `BigInt` s'il doit comparer).

Chaque montant est exposé deux fois :
  - `prix` — déjà formaté dans la notation du jeu (`2.71 PØ`), à afficher ;
  - `prix_brut` — les chiffres seuls, pour trier ou calculer.

Trier sur la forme formatée donnerait un ordre absurde (« 2.71 PØ » avant
« 124.47 GØ » en lexicographique), d'où le doublon plutôt qu'un seul champ.

Ce module ne connaît ni Discord ni HTTP : `src/api.py` l'appelle, les tests
l'appellent directement.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from src.db import plafond_fourchette
from src.money import format_money, format_money_brut, format_money_long
from src.promos import Meta, Promo

def _montants(promo: Promo) -> dict[str, Decimal]:
    """Montants d'une promotion exposés au site, par nom de champ."""
    batiment = promo.building
    return {
        "prix": promo.prix,
        "prix_origine": promo.prix_origine,
        "economie": promo.economie,
        "loyer": batiment.loyer,
        "charge": batiment.charge,
        "impot": batiment.impot,
        "loyer_net": promo.loyer_net,
        "construction": batiment.construction,
        "embellissement": batiment.embellissement,
        "reparation": batiment.reparation,
        "ecart": promo.ecart,
    }


def montant_en_json(nom: str, valeur: Decimal) -> dict[str, str]:
    """Les trois formes d'un montant, toutes en texte, préfixées par `nom`.

    Mêmes rendus que les placeholders `{prix}` / `{prix_long}` / `{prix_brut}`
    du template : le site et le post Discord ne doivent pas afficher deux
    montants différents pour le même bâtiment.
    """
    return {
        nom: format_money(valeur),
        f"{nom}_long": format_money_long(valeur),
        f"{nom}_brut": format_money_brut(valeur),
    }


def promo_en_json(promo: Promo) -> dict[str, Any]:
    """Une promotion, prête à être sérialisée par `json.dumps`."""
    batiment = promo.building
    rendu: dict[str, Any] = {
        "nom": batiment.nom,
        "type": batiment.type,
        # Un niveau tient sur deux chiffres : le seul entier qu'on laisse en
        # `number`, sans risque d'arrondi.
        "niveau": batiment.niveau,
        "remise": f"{promo.remise.normalize():f} %",
        "remise_brut": f"{promo.remise.normalize():f}",
        "rang": promo.rang,
        "total": promo.total,
        # Le site peut vouloir distinguer une promo repêchée, même si le post
        # Discord ne le signale plus.
        "dans_fourchette": bool(promo.dans_fourchette),
        # Trois valeurs là où `dans_fourchette` n'en distingue que deux : une
        # promo tolérée et une repêchée sont toutes deux hors fourchette, mais
        # la première a été choisie, la seconde subie.
        "zone": str(promo.zone),
    }

    for nom, valeur in _montants(promo).items():
        rendu.update(montant_en_json(nom, valeur))

    return rendu


def promos_en_json(promos: list[Promo], meta: Meta, date: str) -> dict[str, Any]:
    """La liste du jour, avec l'en-tête de l'export.

    `meta` accompagne la liste plutôt que chaque promotion : le monde et la date
    de mise à jour sont les mêmes pour toutes, et les répéter grossirait la
    réponse sans rien apporter.
    """
    return {
        "date": date,
        "monde": meta.monde,
        "taux_promoteur": meta.taux_promoteur,
        "mise_a_jour": meta.mise_a_jour,
        "total": len(promos),
        "promos": [promo_en_json(promo) for promo in promos],
    }


def _montant_ou_zero(brut: Any) -> Decimal:
    """Lit un montant de la config, qui y est stocké en texte.

    Tolère la notation scientifique (`1e14`, le défaut d'usine) : la config est
    écrite par `str(Decimal)`, qui la produit pour les grandes valeurs.
    """
    try:
        return Decimal(str(brut))
    except (InvalidOperation, ValueError, TypeError):
        # Config abîmée à la main : mieux vaut afficher 0 Ø que casser la page
        # de réglages, seule façon de la corriger.
        return Decimal(0)


def fourchette_en_json(fourchette: dict) -> dict[str, Any]:
    """Une fourchette : son nom, ses salons, ses bornes en trois formes.

    Les salons sont **dans** la fourchette et non à côté : c'est ce qui permet
    au site de dire quel salon reçoit quelles promotions.

    Les bornes tolérées ne sont exposées **que si elles existent** : les rendre
    à `0 Ø` donnerait à voir une zone que personne n'a réglée, et qui, étant
    sous `prix_min`, prétendrait élargir la fourchette vers le bas.
    """
    rendu: dict[str, Any] = {
        "nom": str(fourchette.get("nom", "")),
        "salons": [str(salon) for salon in fourchette.get("salons") or [] if salon],
    }
    for champ in ("prix_min", "prix_max"):
        rendu.update(
            montant_en_json(champ, _montant_ou_zero(fourchette.get(champ, 0)))
        )
    for champ in ("tolere_min", "tolere_max"):
        if str(fourchette.get(champ) or "").strip():
            rendu.update(
                montant_en_json(champ, _montant_ou_zero(fourchette[champ]))
            )
    # Même règle, et un entier nu : le plafond est un compte, pas un montant, donc
    # rien à pré-formater. Absent quand il n'y en a pas — un `0` se lirait comme
    # une fourchette plafonnée à zéro, c'est-à-dire muette.
    if (plafond := plafond_fourchette(fourchette)) is not None:
        rendu["plafond"] = plafond
    return rendu


def _roles_en_json(config: dict) -> dict[str, str]:
    """Rôle mentionné par serveur, ids en texte.

    Quand un `role_id` plat existe sans table `roles`, il n'est PLUS étendu :
    le site le reçoit via `role_global` et peut l'afficher comme tel, au lieu
    de mentir en le dupliquant par serveur connu (ce qui casserait le jour du
    déploiement, quand aucun serveur n'est connu).
    """
    table = config.get("roles") or {}
    if table:
        return {str(serveur): str(role) for serveur, role in table.items() if role}
    return {}


def config_en_json(config: dict, fourchettes: list[dict]) -> dict[str, Any]:
    """La configuration, telle que la page de réglages la consomme.

    `fourchettes` est passé à part car `Store.fourchettes()` applique les
    migrations (`salon_id` unique, puis config plate) que la config brute ne
    reflète pas.

    Aucun `prix_min`/`prix_max` à la racine : ils appartiennent désormais à une
    fourchette. Les exposer quand même les ferait alimenter par les défauts
    d'usine, donc afficher une fourchette plausible que personne n'a réglée.

    `roles`, `serveurs` et `salons_connus` exposent un rôle par serveur et les
    noms mémorisés en base au moment du réglage, plutôt qu'une résolution Discord
    qui ne dirait pas quel salon vit sur quel serveur.

    `role_global` expose le `role_id` plat d'avant le multi-serveurs : ce rôle
    pingue sur tous les serveurs, mais la table `roles` ne peut pas le représenter
    (elle dirait « par serveur » alors qu'il est global). Le site l'affiche comme
    tel — « s'applique à tous les serveurs » — au lieu de dire « aucune mention ».
    """
    # Le rôle global : exposer le role_id plat seulement quand la table roles
    # est vide. Une fois qu'elle est remplie, le rôle global n'est plus utilisé.
    table_roles = config.get("roles") or {}
    role_id_plat = config.get("role_id")
    role_global = str(role_id_plat) if (role_id_plat and not table_roles) else None

    return {
        "heure": config.get("heure", ""),
        "fuseau": config.get("fuseau", ""),
        "fourchettes": [fourchette_en_json(f) for f in fourchettes],
        "roles": _roles_en_json(config),
        "role_global": role_global,
        "serveurs": {
            str(serveur): str(nom)
            for serveur, nom in (config.get("serveurs") or {}).items()
            if nom
        },
        "salons_connus": {
            str(salon): {
                "nom": str(details.get("nom", "")),
                "serveur": str(details.get("serveur", "")),
            }
            for salon, details in (config.get("salons_connus") or {}).items()
            if isinstance(details, dict)
        },
        "logs_salon_id": (
            str(config.get("logs_salon_id")) if config.get("logs_salon_id") else None
        ),
        "autorises": [str(m) for m in config.get("autorises") or [] if m],
    }


def etat_en_json(
    pret: bool,
    source: str,
    derniere_publication: str | None,
    persistant: bool,
) -> dict[str, Any]:
    """Santé du bot, pour la page d'accueil du site.

    Volontairement sans le nom du monde ni la date de l'export : les obtenir
    demanderait de télécharger le CSV, donc de rendre lente et faillible la
    route la plus souvent appelée. Ces informations accompagnent déjà
    `/api/promos`, qui doit de toute façon lire l'export.

    `source` arrive déjà masqué par `source.decrire` : la clé d'API n'y figure
    jamais, et ce module ne fait que la recopier.
    """
    return {
        "pret": bool(pret),
        "source": source,
        # Nommé plutôt que booléen : le site doit pouvoir écrire « réglages
        # perdus au redémarrage » quand il n'y a pas de base.
        "stockage": "postgres" if persistant else "memoire",
        "derniere_publication": derniere_publication or None,
    }
