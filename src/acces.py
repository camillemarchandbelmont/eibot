"""Qui a le droit d'utiliser le bot.

Ce module est la **seule** source de vérité sur la question. Il ne dépend ni de
Discord ni de HTTP : les commandes slash (`ArbreProtege`) et l'API web
(`src/api.py`) l'appellent toutes les deux avec les informations dont elles
disposent.

C'est ce qui garantit qu'un membre ajouté par `/config acces ajouter` obtient du
même coup l'accès au site : deux implémentations parallèles finiraient par
diverger, et le site serait le maillon faible.
"""

from __future__ import annotations


def acces_autorise(
    est_admin: bool, membre_id: str | int | None, autorises: list[str]
) -> bool:
    """Vrai si ce membre peut utiliser le bot.

    `est_admin` vient de `guild_permissions.administrator` côté Discord, et des
    permissions renvoyées par l'API Discord côté web. Un administrateur passe
    toujours : il ne peut pas se verrouiller dehors en se retirant de la liste.

    Les ids sont comparés en texte : Discord les donne en `int`, JSONB les
    restitue tels qu'écrits, et un `42 != "42"` refuserait l'accès sans raison
    visible.
    """
    if est_admin:
        return True

    # Session web incomplète, ou id absent : on refuse plutôt que de risquer une
    # correspondance avec une entrée vide de la liste. Une seule garde, et non
    # deux : filtrer *aussi* les entrées vides de `autorises` serait redondant,
    # donc du code qu'aucun test ne pourrait distinguer d'un `pass`.
    if not membre_id:
        return False

    return str(membre_id) in [str(membre) for membre in autorises]


def gere_la_liste(est_admin: bool) -> bool:
    """Vrai si ce membre peut modifier la liste des autorisés.

    Volontairement plus strict qu'`acces_autorise` : un membre autorisé pourrait
    sinon s'ajouter des complices, ou retirer l'administrateur qui l'a nommé.
    """
    return bool(est_admin)
