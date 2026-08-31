"""Mot de passe d'écriture de la page web, et cookie qui évite de le retaper.

Cœur pur : ni HTTP, ni base, ni Discord. Des chaînes entrent, des booléens
sortent — c'est ce qui rend chaque propriété éprouvable.

La page des frais est ouverte pour convertir et fermée pour enregistrer : sans
ça, l'URL suffirait à remplacer les relevés du jour de n'importe quelle
entreprise. Un mot de passe **par entreprise**, donc, puis un cookie pour ne le
taper qu'une fois par navigateur.

Trois partis pris :

**Le mot de passe est tiré par le bot**, pas choisi. Il se lit une fois dans une
réponse éphémère de Discord et se colle dans la page ; personne n'a à s'en
souvenir, donc rien n'oblige à en accepter un faible. Et le tirage évite qu'un
mot de passe réutilisé ailleurs finisse en base.

**Seule son empreinte salée est stockée.** La base est chez un hébergeur, et un
mot de passe lisible en base serait celui de l'entreprise pour quiconque la lit.
PBKDF2-SHA256 de la bibliothèque standard : pas de dépendance à installer sur
l'hébergement gratuit, et le nombre d'itérations reste ajustable.

**Le cookie est signé avec l'empreinte elle-même**, qui sert de clé HMAC. Deux
conséquences voulues : aucun secret de plus à configurer sur Render — un secret
absent aurait fait taire silencieusement les cookies — et changer le mot de passe
invalide tous ceux déjà distribués, sans avoir à tenir la liste des navigateurs
identifiés.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

#: Caractères du mot de passe tiré, sans les ambigus.
#:
#: Ni `O`/`0`, ni `l`/`1`/`I` : il se lit dans Discord et se retape dans un
#: navigateur, et un caractère confondu donnerait un refus qu'on prendrait pour
#: une panne de la page. Minuscules seules, pour la même raison.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

#: Découpe du mot de passe tiré : 4 groupes de 4, séparés par des tirets.
#:
#: Seize caractères de cet alphabet valent près de quatre-vingts bits : de quoi
#: n'avoir à protéger cette page contre rien d'autre que l'essai à l'aveugle. Les
#: groupes ne sont là que pour le relire à l'œil.
GROUPES = 4
PAR_GROUPE = 4

#: Nombre d'itérations de PBKDF2. Cent mille : quelques centaines de
#: millisecondes sur l'hébergement gratuit, une fois par navigateur et par mois.
#: Le nombre est **stocké avec l'empreinte** pour pouvoir l'augmenter plus tard
#: sans invalider celles déjà en base.
ITERATIONS = 100_000

#: Validité du cookie : quatre cents jours **sans enregistrement**.
#:
#: Un délai d'inactivité et non une date de fin : la page repose le cookie à
#: chaque enregistrement, si bien qu'un navigateur qui sert reste identifié sans
#: jamais retaper le mot de passe. C'est le but — un mot de passe qu'il faut
#: garder sous la main est un mot de passe à portée de tout le monde.
#:
#: Quatre cents jours et pas davantage : les navigateurs ramènent d'eux-mêmes à
#: cette durée tout cookie qui demande plus. Un chiffre plus grand ne servirait
#: donc à rien, et ferait disparaître le cookie avant la date pour laquelle il
#: est signé — le mot de passe serait à retaper un jour où rien ne l'annonce.
#:
#: Ce qui coupe un navigateur n'est donc pas le temps mais
#: `/reglages motdepasse` : en tirer un nouveau, ou le retirer, invalide tous les
#: cookies déjà distribués, puisque l'empreinte leur sert de clé de signature.
DUREE_JETON = 400 * 24 * 3600

_ALGO = "pbkdf2_sha256"


def nouveau() -> str:
    """Un mot de passe tiré au hasard, lisible et retapable.

    `secrets` et non `random` : le second est prévisible à partir de quelques
    tirages, ce qui rendrait tous les mots de passe suivants.
    """
    groupes = (
        "".join(secrets.choice(ALPHABET) for _ in range(PAR_GROUPE))
        for _ in range(GROUPES)
    )
    return "-".join(groupes)


def empreinte(mot_de_passe: str) -> dict[str, str | int]:
    """Ce qui part en base : sel, empreinte, algorithme et itérations.

    L'algorithme et les itérations sont écrits à côté plutôt que lus dans le code
    au moment de vérifier : les augmenter invaliderait sinon toutes les empreintes
    déjà enregistrées, et chaque entreprise se retrouverait sans mot de passe sans
    que rien ne le dise.
    """
    sel = secrets.token_bytes(16)
    calcul = hashlib.pbkdf2_hmac(
        "sha256", str(mot_de_passe).encode(), sel, ITERATIONS
    )
    return {
        "algo": _ALGO,
        "iterations": ITERATIONS,
        "sel": sel.hex(),
        "empreinte": calcul.hex(),
    }


def verifie(trace: dict | None, mot_de_passe: str) -> bool:
    """Vrai si le mot de passe correspond à l'empreinte enregistrée.

    Faux — et non une exception — dans tous les cas douteux : pas d'empreinte,
    empreinte abîmée par une retouche à la main, mot de passe vide. La
    configuration est du JSON éditable, et une entreprise sans mot de passe doit
    être **fermée** plutôt qu'ouverte à tous, ce que produirait la comparaison
    `"" == ""`.

    `compare_digest` plutôt que `==` : la comparaison native s'arrête au premier
    octet différent, ce qui laisse mesurer le préfixe correct.
    """
    if not isinstance(trace, dict):
        return False
    saisi = str(mot_de_passe or "").strip()
    if not saisi:
        return False

    try:
        sel = bytes.fromhex(str(trace.get("sel", "")))
        attendue = bytes.fromhex(str(trace.get("empreinte", "")))
        iterations = int(trace.get("iterations", ITERATIONS))
    except (TypeError, ValueError):
        return False
    if not sel or not attendue or iterations < 1:
        return False

    calcul = hashlib.pbkdf2_hmac("sha256", saisi.encode(), sel, iterations)
    return hmac.compare_digest(calcul, attendue)


def _signature(trace: dict, serveur_id: str, expiration: int) -> str:
    """HMAC de `<serveur>|<expiration>`, l'empreinte servant de clé.

    L'entreprise **est dans le message signé** : sans elle, le mot de passe d'une
    entreprise vaudrait pour toutes les autres, dont la page propose la liste dans
    un menu déroulant. L'expiration aussi, sinon la prolonger serait une retouche
    de texte et un cookie volé vaudrait pour toujours.
    """
    cle = str(trace.get("empreinte", "")).encode()
    message = f"{serveur_id}|{expiration}".encode()
    return hmac.new(cle, message, hashlib.sha256).hexdigest()


def signer(trace: dict, serveur_id: str | int, expiration: int) -> str:
    """Le contenu du cookie : `<expiration>.<signature>`.

    L'expiration voyage en clair — il faut la lire pour la comparer — mais elle
    est signée. L'empreinte, elle, n'y est pas : un cookie se lit dans les outils
    du navigateur, et la clé permettrait d'en forger d'autres.
    """
    return f"{int(expiration)}.{_signature(trace, str(serveur_id), int(expiration))}"


def verifier_jeton(
    trace: dict | None, jeton: str, serveur_id: str | int, maintenant: int
) -> bool:
    """Vrai si le cookie autorise à écrire dans cette entreprise, maintenant.

    La date est vérifiée ici et pas seulement laissée au navigateur : un cookie se
    rejoue à la main longtemps après que le navigateur l'aurait effacé.
    """
    if not isinstance(trace, dict) or not trace.get("empreinte"):
        return False

    brut, point, signature = str(jeton or "").partition(".")
    if not point or not signature:
        return False
    try:
        expiration = int(brut)
    except ValueError:
        return False
    if expiration <= int(maintenant):
        return False

    attendue = _signature(trace, str(serveur_id), expiration)
    return hmac.compare_digest(signature, attendue)
