"""Frais de gestion par filiale : cœur pur, sans Discord ni base.

Deux règles viennent du jeu et non de nous :

  - les frais valent 7 % des bénéfices (`money.frais_de_gestion`), sans
    décimales — le jeu ne facture pas de fraction d'Ø ;
  - une filiale qui ne gagne rien ne paie rien. Le jeu ne rembourse pas : des
    frais négatifs seraient un montant inventé. Zéro compte comme une perte,
    puisqu'il n'y a rien à prélever dessus.

Le **nom est la clé d'import du jeu** : il est conservé caractère pour
caractère, doubles espaces compris (`ARMEE  DE TERRE`). Seuls les espaces de
bordure sont retirés, parce qu'ils sont invisibles et viennent d'un copier-coller.
La comparaison, elle, ignore la casse : deux lignes que rien ne distingue à
l'œil seraient pires qu'un remplacement.

Les montants ne traversent jamais un `float` : ils atteignent dix-sept chiffres,
et JSONB n'a pas d'entier de cette taille — d'où le stockage en chaîne.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from random import Random
from typing import Any

from src.money import format_money_brut, frais_de_gestion


class FilialeError(ValueError):
    """Saisie de filiale inutilisable."""


@dataclass(frozen=True)
class Filiale:
    """Un relevé : ce qu'une filiale a rapporté, et ce qu'elle coûte."""

    nom: str          #: tel que saisi, espaces internes compris
    benefices: Decimal
    frais: Decimal    #: 7 % des bénéfices, ou 0 quand elle ne gagne rien
    date: str         #: 'AAAA-MM-JJ' de la saisie

    @property
    def en_perte(self) -> bool:
        """Vrai quand il n'y a rien à prélever (bénéfices nuls ou négatifs)."""
        return self.benefices <= 0


def _cle(nom: str) -> str:
    """Forme comparable d'un nom : casse ignorée, bordures retirées.

    Les espaces **internes** restent : le jeu les compte dans sa clé d'import.
    """
    return str(nom).strip().casefold()


def calculer(nom: str, benefices: Decimal, date: str) -> Filiale:
    """Relevé d'une filiale à partir de ses bénéfices.

    Lève `FilialeError` si le nom est vide : une ligne anonyme serait
    impossible à retrouver dans le tableau comme à remplacer par une ressaisie.
    """
    propre = str(nom).strip()
    if not propre:
        raise FilialeError("Le nom de la filiale ne peut pas être vide.")

    montant = Decimal(benefices)
    # `frais_de_gestion` est appelée et non recopiée : le taux affiché par
    # `/convertir frais` et celui appliqué ici ne peuvent donc pas divergier.
    frais = frais_de_gestion(montant) if montant > 0 else Decimal(0)
    return Filiale(nom=propre, benefices=montant, frais=frais, date=str(date))


def index_de(filiales: list[Filiale], nom: str) -> int:
    """Position d'une filiale par son nom, -1 si absente."""
    cible = _cle(nom)
    for index, filiale in enumerate(filiales):
        if _cle(filiale.nom) == cible:
            return index
    return -1


def enregistrer(filiales: list[Filiale], filiale: Filiale) -> list[Filiale]:
    """Liste augmentée du relevé, en remplaçant celui de la même filiale.

    Le remplacement conserve la **place** de la filiale : la faire remonter en
    fin de liste ferait danser le tableau d'un jour à l'autre.

    Renvoie une nouvelle liste : la liste d'entrée vient de la base, et la muter
    en place ferait divergier ce qui est affiché de ce qui est enregistré si
    l'écriture échouait ensuite.
    """
    index = index_de(filiales, filiale.nom)
    if index < 0:
        return [*filiales, filiale]
    return [*filiales[:index], filiale, *filiales[index + 1 :]]


def retirer(filiales: list[Filiale], nom: str) -> list[Filiale]:
    """Liste privée d'une filiale. Inchangée si elle n'y était pas."""
    index = index_de(filiales, nom)
    if index < 0:
        return list(filiales)
    return [*filiales[:index], *filiales[index + 1 :]]


def noms_separes(saisie: str) -> list[str]:
    """Découpe une saisie en noms de filiales, sur les virgules et les lignes.

    Discord n'offre pas de champ répétable : un retrait de masse passe donc par
    une chaîne, où les noms arrivent séparés par des virgules ou collés d'une
    liste, un par ligne.

    Les espaces de bordure partent — ils sont invisibles à la saisie, et un nom
    qui les garderait ne correspondrait à aucune filiale. Les **internes**
    restent, doubles compris : c'est la clé d'import du jeu.

    Les répétitions sont écartées, sans égard à la casse : deux fois le même nom
    compterait deux retraits, ou un « inconnu » pour un nom déjà retiré. Le
    premier écrit est celui qui reste, puisque c'est celui qu'on relira.
    """
    noms: list[str] = []
    vus: set[str] = set()
    for morceau in str(saisie).replace("\n", ",").split(","):
        nom = morceau.strip()
        if not nom or _cle(nom) in vus:
            continue
        vus.add(_cle(nom))
        noms.append(nom)
    return noms


def retirer_plusieurs(filiales: list[Filiale], noms: list[str]) -> list[Filiale]:
    """Liste privée de toutes les filiales nommées, d'un seul geste.

    Un nom inconnu est ignoré plutôt que fatal : la commande dira lesquels
    elle n'a pas trouvés, mais une faute de frappe ne doit pas annuler les
    retraits valides de la même saisie.

    Une liste de noms vide ne retire rien. C'est probablement une saisie ratée,
    et tout effacer serait la pire interprétation possible.
    """
    cibles = {_cle(nom) for nom in noms}
    return [filiale for filiale in filiales if _cle(filiale.nom) not in cibles]


def remettre_a_zero(filiales: list[Filiale], date: str) -> list[Filiale]:
    """Mêmes filiales, mêmes noms, bénéfices remis à zéro.

    Les noms sont la clé d'import du jeu et l'assise de l'autocomplétion : les
    garder fait qu'un nouveau cycle ne demande que de ressaisir les montants.
    À zéro, chaque filiale compte comme en perte et le total retombe à 0 Ø.

    La date est celle de la remise, non celle du relevé effacé : c'est un fait
    nouveau, et garder l'ancienne ferait passer la ligne pour périmée le jour
    même où on vient de la remettre.
    """
    return [
        calculer(filiale.nom, Decimal(0), date) for filiale in filiales
    ]


#: Bornes du tirage d'essai, en nombre de chiffres.
#:
#: Vingt-un chiffres en haut : c'est la taille des bénéfices du Mégapôle, et
#: au-delà de la mantisse d'un `float64` — un essai plafonné plus bas ne mettrait
#: jamais à l'épreuve ce que la production doit encaisser. Trois en bas pour que
#: le tableau soit vu avec des lignes de tailles très différentes, seul moyen
#: d'éprouver à la fois son tri et les notations d'échelle du jeu.
CHIFFRES_ESSAI = (3, 21)

#: Une filiale sur cinq est tirée en perte.
#:
#: Une perte se marque autrement dans le tableau : sans aucune, la moitié de
#: l'affichage resterait invisible à l'essai. Une sur cinq, et non une sur deux,
#: pour que l'essai ressemble à un vrai jour.
PART_EN_PERTE = 0.2


def benefices_aleatoires(alea: Random, exposant: int | None = None) -> Decimal:
    """Un montant de bénéfices tiré au hasard, pour voir le tableau à l'essai.

    Le générateur est **passé** et non pris au module : sans lui, un test ne
    pourrait pas rejouer un tirage, donc ne pourrait rien affirmer dessus.

    Sans `exposant`, le tirage porte d'abord sur le **nombre de chiffres**, puis
    sur la valeur : tirer directement entre 1 et 10²¹ ne donnerait pratiquement
    que des montants de vingt-et-un chiffres, et toutes les lignes
    s'afficheraient dans la même échelle.

    Avec un `exposant`, le montant tombe entre 1 et 999 fois ce palier — les
    bornes de `format_money`, qui rebascule sur le symbole du dessus à 1000. On
    voit alors le tableau dans l'unité choisie plutôt qu'à travers toute
    l'échelle.

    `Decimal` et jamais `float` : un flottant perdrait les derniers chiffres d'un
    montant de vingt-un chiffres, et l'essai afficherait un nombre que le jeu ne
    connaît pas.
    """
    if exposant is None:
        bas, haut = CHIFFRES_ESSAI
        chiffres = alea.randint(bas, haut)
        montant = Decimal(alea.randrange(10 ** (chiffres - 1), 10**chiffres))
    else:
        palier = 10**exposant
        # `format_money` arrondit la mantisse à deux décimales **avant** de
        # choisir le symbole : au-delà de 999,995 fois le palier elle atteint
        # 1000,00 et rebascule sur le palier du dessus, si bien qu'un tirage
        # « en PØ » s'afficherait parfois en EØ. Le plafond s'arrête donc juste
        # sous cette bascule (le retrait est nul à l'unité, où le jeu n'affiche
        # pas de décimales).
        haut = 1000 * palier - palier // 200
        # Depuis 1 fois le palier, pour que le symbole choisi soit bien celui
        # qui s'affiche ; sous cette borne, `format_money` prendrait celui du
        # dessous. À l'unité, où il n'y a rien en dessous, on part de 1 Ø.
        montant = Decimal(alea.randrange(palier, haut))
    if alea.random() < PART_EN_PERTE:
        return -montant
    return montant


def valeurs_aleatoires(
    filiales: list[Filiale], date: str, alea: Random, exposant: int | None = None
) -> list[Filiale]:
    """Mêmes filiales, montants tirés au hasard : le tableau à l'essai.

    Les noms et leur ordre sont conservés — ils sont la clé d'import du jeu, et
    un essai ne doit pas obliger à tous les ressaisir ensuite. Seuls les
    montants changent, et les frais sont recalculés par `calculer` : affichés
    sans rapport avec les bénéfices d'à côté, ils ne prouveraient rien.

    `exposant` borne le tirage à un palier du jeu ; sans lui, il couvre toute
    l'échelle.

    La date est celle de l'essai : datées de la veille, toutes les lignes
    s'afficheraient comme périmées et le tableau ne serait pas vu tel qu'il sort
    d'ordinaire.
    """
    return [
        calculer(filiale.nom, benefices_aleatoires(alea, exposant), date)
        for filiale in filiales
    ]


#: Séparateur de colonnes du format d'import du jeu.
#:
#: Une tabulation, et une seule par ligne : le jeu refuse la ligne au-delà. Elle
#: ne se tape pas dans Discord — la touche y sert à l'autocomplétion et n'insère
#: rien — d'où la sortie en pièce jointe, seul véhicule dont Discord ne retouche
#: pas les octets.
SEPARATEUR_IMPORT = "\t"

#: Fin de ligne du format d'import : CRLF, séparateur officiel du jeu.
#:
#: Le contenu d'un message Discord ne peut pas la porter — les fins de ligne y
#: sont normalisées — ce qui exclut le bloc de code et impose le fichier.
FIN_DE_LIGNE_IMPORT = "\r\n"


def nom_pour_import(nom: str) -> str:
    """Nom utilisable dans une ligne tab-séparée.

    Les espaces **internes** restent, doubles compris : c'est la clé d'import du
    jeu, et les normaliser ferait échouer la correspondance de son côté.

    Seuls tabulations et retours à la ligne partent, remplacés par un espace.
    Ils ne se tapent pas dans Discord mais s'y **collent** — `calculer` ne retire
    que les bordures — et un nom qui en porterait ouvrirait une deuxième colonne
    ou couperait la ligne, si bien que le jeu refuserait la ligne entière.

    Exposée plutôt que cachée dans `vers_import` : la commande s'en sert pour
    dire quels noms elle a dû modifier, et recopier la règle là-bas ferait
    divergier ce qui est annoncé de ce qui est écrit.
    """
    propre = str(nom)
    for casseur in (SEPARATEUR_IMPORT, "\r\n", "\r", "\n"):
        propre = propre.replace(casseur, " ")
    return propre


def vers_import(filiales: list[Filiale]) -> str:
    """Le tableau au format d'import du jeu : nom, tabulation, frais, CRLF.

    Les frais et non les bénéfices : le format réclame ce qu'on doit. Les
    confondre ferait payer quatorze fois trop sans que le fichier ait l'air faux.

    Les filiales en perte y sont, à zéro — une ligne par filiale, et zéro est le
    montant exact puisqu'il n'y a rien à prélever sur une perte. Omises, le
    fichier passerait pour un export incomplet.

    Les montants passent par `format_money_brut` et non `format_money` : un
    arrondi à deux décimales ferait importer `9,67 EØ` au lieu du montant au
    chiffre près.

    L'ordre enregistré est gardé plutôt qu'un tri : le fichier est une entrée
    machine, pas un classement, et trié, deux exports des mêmes filiales
    différeraient dès qu'un montant bouge.

    Sans filiale, la chaîne est vide et non un CRLF solitaire, que le jeu lirait
    comme une ligne sans nom.
    """
    return "".join(
        f"{nom_pour_import(filiale.nom)}"
        f"{SEPARATEUR_IMPORT}"
        f"{format_money_brut(filiale.frais)}"
        f"{FIN_DE_LIGNE_IMPORT}"
        for filiale in filiales
    )


def total_frais(filiales: list[Filiale]) -> Decimal:
    """Somme des frais. `Decimal` de bout en bout : dix-sept chiffres."""
    return sum((filiale.frais for filiale in filiales), Decimal(0))


def vers_json(filiales: list[Filiale]) -> list[dict]:
    """Forme stockable en JSONB. Les montants sont des **chaînes**.

    Un nombre JSON passerait par un flottant et perdrait ses derniers chiffres.
    """
    return [
        {
            "nom": filiale.nom,
            "benefices": str(filiale.benefices),
            "frais": str(filiale.frais),
            "date": filiale.date,
        }
        for filiale in filiales
    ]


def depuis_json(brut: Any) -> list[Filiale]:
    """Relit ce que `vers_json` a écrit, en sautant les lignes illisibles.

    La config est du JSON retouchable à la main : une ligne cassée doit coûter
    sa filiale, pas le tableau du jour.

    Les frais sont **recalculés** depuis les bénéfices plutôt que relus : s'ils
    ne collaient plus (retouche à la main, taux modifié), le tableau annoncerait
    un montant que le jeu ne réclamera pas.
    """
    filiales: list[Filiale] = []
    for ligne in brut or []:
        if not isinstance(ligne, dict):
            continue
        try:
            filiales.append(
                calculer(
                    ligne.get("nom", ""),
                    Decimal(str(ligne.get("benefices", ""))),
                    str(ligne.get("date", "")),
                )
            )
        except (FilialeError, InvalidOperation, ArithmeticError):
            continue
    return filiales
