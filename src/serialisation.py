"""Conversion des objets métier en JSON, pour l'API du site web.

**Aucun montant ne traverse le JSON en `number`.** Un `number` JSON est un
double IEEE 754 : `138131471904669765329` en ressortirait à
`138131471904669800000`, et le site afficherait un prix faux sans que rien ne
signale l'erreur. Tout montant est donc une **chaîne de chiffres**, que le site
relit tel quel (ou via `BigInt` s'il doit comparer).

Chaque montant est exposé deux fois :
  - `prix` — déjà formaté dans la notation du jeu (`2,71 PØ`), à afficher ;
  - `prix_brut` — les chiffres seuls, pour trier ou calculer.

Trier sur la forme formatée donnerait un ordre absurde (« 2,71 PØ » avant
« 124,47 GØ » en lexicographique), d'où le doublon plutôt qu'un seul champ.

Ce module ne connaît ni Discord ni HTTP : `src/api.py` l'appelle, les tests
l'appellent directement.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

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
    """
    rendu: dict[str, Any] = {
        "nom": str(fourchette.get("nom", "")),
        "salons": [str(salon) for salon in fourchette.get("salons") or [] if salon],
    }
    for champ in ("prix_min", "prix_max"):
        rendu.update(
            montant_en_json(champ, _montant_ou_zero(fourchette.get(champ, 0)))
        )
    return rendu


def config_en_json(config: dict, fourchettes: list[dict]) -> dict[str, Any]:
    """La configuration, telle que la page de réglages la consomme.

    `fourchettes` est passé à part car `Store.fourchettes()` applique les
    migrations (`salon_id` unique, puis config plate) que la config brute ne
    reflète pas.

    Aucun `prix_min`/`prix_max` à la racine : ils appartiennent désormais à une
    fourchette. Les exposer quand même les ferait alimenter par les défauts
    d'usine, donc afficher une fourchette plausible que personne n'a réglée.
    """
    return {
        "heure": config.get("heure", ""),
        "fuseau": config.get("fuseau", ""),
        "fourchettes": [fourchette_en_json(f) for f in fourchettes],
        # `or None` : la config stocke indifféremment "" ou None pour « aucun »,
        # et le site n'a pas à distinguer les deux.
        "role_id": str(config.get("role_id")) if config.get("role_id") else None,
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
