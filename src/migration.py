"""Déménagement de l'état du bot d'une base Postgres à une autre.

Sert une fois : la base gratuite de Render expire, l'état part sur Supabase. Il
tient dans une table de cinq clés JSONB, ce qui rend la copie triviale — mais
`pg_dump` n'est pas installé sur cette machine, et l'installer pour cinq lignes
serait plus lourd que ce module.

Ce module **recopie**, il ne déplace pas : la base de départ reste intacte, seul
recours si celle d'arrivée se révèle inutilisable après coup.

Les chaînes de connexion ne sont pas lues dans l'environnement mais **demandées
à l'écran, masquées** (`python -m src.migration`) : mises dans `.env`, elles y
resteraient — et un `.env` qui porte le `DATABASE_URL` de production ferait
écrire au bot de test dans la config de production, la clé `config` étant unique
et globale.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from src.db import Store

log = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    """Déménagement impossible ou trop risqué pour être tenté."""


def sans_mot_de_passe(dsn: str) -> str:
    """Chaîne de connexion affichable : tout sauf le mot de passe.

    Dire de quelle base on parle est nécessaire — deux DSN se ressemblent, et
    c'est justement la confusion qui coûterait les données. Mais ce qui s'affiche
    part dans l'historique du terminal et dans tout copier-coller vers un ticket.

    Une chaîne qui ne se découpe pas en URL est masquée **en entier** : ne pas
    savoir où est le secret, c'est exactement le cas où la fuite passerait
    inaperçue.
    """
    try:
        morceaux = urlsplit(str(dsn))
    except ValueError:
        return "***"
    if not morceaux.netloc:
        return "***"
    if morceaux.password is None:
        return str(dsn)

    identite = morceaux.username or ""
    hote = morceaux.hostname or ""
    if morceaux.port:
        hote = f"{hote}:{morceaux.port}"
    return urlunsplit(morceaux._replace(netloc=f"{identite}:***@{hote}"))


def verifier_deux_bases(dsn_source: str, dsn_cible: str) -> None:
    """Refuse un déménagement qui n'irait nulle part.

    Deux pièges, tous deux silencieux et tous deux fatals :

      - **la même chaîne deux fois.** La copie réussirait, sans écart, et la base
        de départ serait éteinte ensuite avec les données dedans.
      - **une chaîne vide.** `Store` retombe alors en mémoire sans lever :
        la cible relirait fidèlement ce qu'on vient d'y écrire, donc zéro écart,
        puis tout disparaîtrait à la fin du processus.

    Les deux rendraient un rapport de réussite. C'est pour ça que la garde est
    ici plutôt que dans un commentaire du mode d'emploi.
    """
    if not str(dsn_source).strip():
        raise MigrationError("Chaîne de connexion de départ vide.")
    if not str(dsn_cible).strip():
        raise MigrationError("Chaîne de connexion d'arrivée vide.")
    if str(dsn_source).strip() == str(dsn_cible).strip():
        raise MigrationError(
            "Les deux chaînes sont identiques "
            f"({sans_mot_de_passe(dsn_source)}) : rien à déménager."
        )


def ecarts(source: dict[str, Any], cible: dict[str, Any]) -> list[str]:
    """Clés de la source que la cible ne rend pas à l'identique.

    Sert à **relire** la copie : une base peut accepter une écriture et n'en rien
    garder — droits insuffisants, réplique en lecture seule. Annoncer une
    réussite sur la foi des seules écritures ne prouverait rien, et le bot
    redémarrerait sur une config d'usine.

    Ce que la cible a **en plus** est ignoré : la copie promet d'y porter la
    source, pas de la vider. Le compter ferait échouer un `forcer` valide.
    """
    return [cle for cle, valeur in source.items() if cible.get(cle) != valeur]


async def copier(source: Store, cible: Store, forcer: bool = False) -> list[str]:
    """Recopie tout l'état de `source` dans `cible`, puis relit. Rend les écarts.

    Toutes les clés **trouvées**, sans liste écrite en dur : une clé ajoutée
    depuis serait sinon laissée derrière, et le manque ne se verrait qu'une fois
    l'ancienne base éteinte. Les marques de publication en font partie — sans
    elles, le bot republierait le tableau du jour, ce qui se lirait comme une
    double facturation.

    Une cible **non vide** est refusée sauf `forcer` : elle peut déjà servir à un
    autre bot ou à un essai, et l'écrasement serait irréversible. Le refus est
    total, rien n'ayant été écrit avant la vérification.

    `forcer` existe parce qu'une première passe peut avoir échoué à mi-chemin :
    sans lui, il faudrait vider la table à la main entre deux tentatives, au
    risque de la vider une fois la copie réussie.

    La liste rendue est vide quand tout est arrivé. Elle n'est pas levée en
    exception : savoir **quelles** clés manquent vaut mieux qu'un échec sec, la
    suite du déménagement se jouant clé par clé.
    """
    etat = await source.tout()

    deja = await cible.tout()
    if deja and not forcer:
        raise MigrationError(
            f"La base d'arrivée contient déjà {len(deja)} clé(s) "
            f"({', '.join(sorted(deja))}). Relancer avec forcer=True pour écraser."
        )

    for cle, valeur in etat.items():
        await cible.set(cle, valeur)

    return ecarts(etat, await cible.tout())


# --- Mode d'emploi interactif ----------------------------------------------


def _demander(question: str) -> str:
    """Une chaîne de connexion, saisie sans être affichée.

    `getpass` et non `input` : la chaîne porte le mot de passe de la base, et
    `input` le laisserait à l'écran, donc dans le défilement du terminal.

    Importé ici et non en tête : le module est aussi importé par les tests, qui
    n'ont rien à faire d'un terminal.
    """
    from getpass import getpass

    return getpass(question).strip()


async def _executer(dsn_source: str, dsn_cible: str, forcer: bool) -> int:
    """Le déménagement d'un bout à l'autre. Rend le code de sortie du processus.

    Les deux bases sont fermées dans un `finally` : une copie interrompue
    laisserait sinon des connexions ouvertes sur la base d'arrivée, et Supabase
    en compte peu sur le plan gratuit.
    """
    verifier_deux_bases(dsn_source, dsn_cible)

    source = Store(dsn=dsn_source)
    cible = Store(dsn=dsn_cible)
    try:
        await source.connect()
        await cible.connect()

        etat = await source.tout()
        if not etat:
            print(
                "Aucune clé dans la base de départ "
                f"({sans_mot_de_passe(dsn_source)}) : rien à déménager."
            )
            return 1

        print(f"À déménager : {', '.join(sorted(etat))}")
        manquantes = await copier(source, cible, forcer=forcer)
        if manquantes:
            # Pas de « c'est bon » : la base d'arrivée n'a pas gardé ce qu'on lui
            # a donné, et éteindre l'ancienne là-dessus perdrait ces clés.
            print(f"⚠️ Non relu dans la base d'arrivée : {', '.join(manquantes)}")
            return 1

        print(f"{len(etat)} clé(s) recopiée(s) et relue(s). La base de départ est intacte.")
        return 0
    finally:
        await source.close()
        await cible.close()


def main(argv: list[str] | None = None) -> int:
    """`python -m src.migration [--forcer]`.

    Les deux chaînes sont demandées à l'écran plutôt que prises dans
    l'environnement : passées en argument, elles resteraient dans l'historique du
    shell, et posées dans `.env`, elles y resteraient tout court.
    """
    import sys

    forcer = "--forcer" in (argv if argv is not None else sys.argv[1:])

    print("Déménagement de l'état du bot d'une base Postgres à une autre.")
    print("Les chaînes ne s'affichent pas à la saisie ; rien n'est enregistré.")
    dsn_source = _demander("Chaîne de connexion de DÉPART (Render) : ")
    dsn_cible = _demander("Chaîne de connexion d'ARRIVÉE (Supabase, session pooler) : ")

    try:
        return asyncio.run(_executer(dsn_source, dsn_cible, forcer))
    except MigrationError as erreur:
        print(f"Refusé : {erreur}")
        return 2
    except Exception as erreur:  # noqa: BLE001
        # Le type seul : un message d'asyncpg peut recopier la chaîne de
        # connexion, mot de passe compris.
        print(f"Échec ({type(erreur).__name__}). Aucune donnée perdue côté départ.")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
