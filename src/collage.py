"""Lecture du tableau des filiales collé depuis le jeu.

Cœur pur : ni Discord, ni base, ni HTTP. Une chaîne entre, des relevés sortent.

Le jeu affiche ses filiales dans un tableau — `Filiale`, `Trésorerie`, `Résultat
d'exploitation`, `Résultat NET`, `Bénéfices ou pertes` — que l'on sélectionne à
la souris et que l'on colle. Ce module en tire ce que le bot sait déjà traiter :
un nom et des bénéfices, que `filiales.calculer` transforme en relevés.

**Les colonnes se séparent aux tabulations, et à rien d'autre.** C'est ce que le
navigateur pose en collant un tableau, et c'est la seule règle qui survit aux
vraies données :

- les noms contiennent des doubles espaces (`ARMEE  DE TERRE`), que le jeu compte
  dans sa clé d'import : découper sur les espaces couperait le nom en deux ;
- des noms finissent par un chiffre (`EMF AZOU 1`) : ramasser les nombres depuis
  la droite emporterait ce chiffre et enregistrerait une filiale « EMF AZOU »,
  inconnue du jeu.

Les deux fautes sont silencieuses — un import qui ne met rien à jour ressemble à
un import réussi. D'où le parti pris : une ligne sans tabulation est **refusée et
montrée**, jamais devinée.

Rien n'est refusé en bloc : une ligne illisible coûte sa filiale, pas le collage.
Recoller les treize lignes du jeu pour une faute de frappe serait pire que sauter
celle-ci en la nommant.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.filiales import Filiale, calculer, enregistrer
from src.money import MoneyError, parse_money

#: Séparateur de colonnes d'un collage de tableau. Le même que celui du format
#: d'import du jeu (`filiales.SEPARATEUR_IMPORT`), par coïncidence et non par
#: dépendance : celui-là décrit ce que le jeu accepte, celui-ci ce que le
#: navigateur produit.
SEPARATEUR = "\t"

#: Première colonne du tableau du jeu, en minuscules. Sert à reconnaître la ligne
#: de titres, qui fait partie de toute sélection commencée en haut du tableau.
TITRE_PREMIERE_COLONNE = "filiale"

#: Accents à neutraliser pour reconnaître « Bénéfices ou pertes ».
#:
#: Une table plutôt qu'`unicodedata` : deux lettres à couvrir, et l'on voit ici
#: exactement ce qui est comparé.
_SANS_ACCENTS = str.maketrans("éèêëÉÈÊË", "eeeeEEEE")


@dataclass(frozen=True)
class Releve:
    """Une ligne lue : de quoi appeler `filiales.calculer`."""

    nom: str            #: tel que collé, espaces internes compris
    benefices: Decimal  #: négatif quand la filiale est en perte


@dataclass(frozen=True)
class Refus:
    """Une ligne que le collage contenait et que l'on n'a pas su lire.

    Porte son numéro **dans le collage** et son texte : la page les affiche pour
    qu'on retrouve la ligne dans la zone de texte et qu'on la corrige.
    """

    numero: int
    ligne: str
    raison: str


@dataclass(frozen=True)
class Lecture:
    """Ce qu'un collage a donné : ce qui est lu, et ce qui ne l'est pas."""

    releves: list[Releve]
    refuses: list[Refus]


def _est_entete(cellules: list[str]) -> bool:
    """Vrai pour la ligne de titres du jeu.

    Reconnue à sa première colonne, `Filiale`, et non à l'absence de montants :
    une ligne dont le montant est illisible doit être signalée, pas prise pour un
    en-tête et effacée en silence.
    """
    return bool(cellules) and cellules[0].strip().casefold() == TITRE_PREMIERE_COLONNE


def _colonne_des_benefices(cellules: list[str]) -> int | None:
    """Rang de la colonne « Bénéfices ou pertes » dans un en-tête, ou rien.

    Cherchée sur `benefice` seul : le jeu écrit « Bénéfices ou pertes », mais un
    intitulé au singulier ou complété d'un mot ne doit pas faire retomber la page
    sur la dernière colonne sans que rien ne le dise.
    """
    for index, cellule in enumerate(cellules):
        if "benefice" in cellule.translate(_SANS_ACCENTS).casefold():
            return index
    return None


def _cellule_des_benefices(cellules: list[str], colonne: int | None) -> str:
    """La cellule où lire les bénéfices, vide si la ligne n'en offre aucune.

    La colonne nommée par l'en-tête d'abord ; à défaut la **dernière** cellule
    remplie, celle des bénéfices dans le tableau du jeu.

    Le repli couvre deux cas ordinaires : une sélection qui n'a pas pris les
    titres, et une ligne plus courte que l'en-tête — celle qu'on tape à la main
    pour corriger une filiale, avec son nom et son montant.

    La première cellule est exclue du repli : c'est le nom, et une ligne à une
    seule colonne doit être refusée plutôt que lue comme un montant.
    """
    if colonne is not None and 0 < colonne < len(cellules):
        if cellules[colonne].strip():
            return cellules[colonne].strip()
    for cellule in reversed(cellules[1:]):
        if cellule.strip():
            return cellule.strip()
    return ""


def lire_collage(texte: str) -> Lecture:
    """Lit un collage du tableau du jeu, ligne par ligne.

    Les lignes vides sont ignorées sans bruit : un collage se termine par un
    retour à la ligne et en contient parfois. Les signaler ferait chercher une
    faute là où il n'y a qu'un passage à la ligne.

    Le numéro d'une ligne refusée est celui du collage, en comptant les lignes
    vides et l'en-tête : compté sur les seules lignes lues, il désignerait la
    mauvaise et l'on corrigerait une filiale saine.
    """
    releves: list[Releve] = []
    refuses: list[Refus] = []
    #: Rang de la colonne des bénéfices, une fois l'en-tête rencontré.
    colonne: int | None = None

    for numero, ligne in enumerate(str(texte or "").splitlines(), start=1):
        if not ligne.strip():
            continue

        cellules = ligne.split(SEPARATEUR)
        if _est_entete(cellules):
            colonne = _colonne_des_benefices(cellules)
            continue

        if len(cellules) < 2:
            refuses.append(Refus(
                numero,
                ligne,
                "Pas de tabulation : colle le tableau du jeu plutôt que de "
                "retaper la ligne, ou sépare le nom du montant par une "
                "tabulation. Les espaces ne peuvent pas servir de séparateur, "
                "les noms de filiales en contiennent.",
            ))
            continue

        nom = cellules[0].strip()
        if not nom:
            refuses.append(Refus(
                numero, ligne, "Pas de nom de filiale dans la première colonne."
            ))
            continue

        brut = _cellule_des_benefices(cellules, colonne)
        if not brut:
            refuses.append(Refus(numero, ligne, f"Aucun montant pour « {nom} »."))
            continue

        try:
            # La grammaire de Discord : `1 000 000`, `2,71 PØ` ou les dix-neuf
            # chiffres bruts du jeu. Une seule façon d'écrire un montant dans
            # tout le bot ; la page ne doit pas inventer la sienne.
            benefices = parse_money(brut)
        except MoneyError as erreur:
            refuses.append(Refus(numero, ligne, str(erreur)))
            continue

        releves.append(Releve(nom=nom, benefices=benefices))

    return Lecture(releves=releves, refuses=refuses)


def vers_filiales(lecture: Lecture, date: str) -> list[Filiale]:
    """Les relevés d'un collage, frais calculés, prêts à afficher ou à enregistrer.

    Passe par `filiales.calculer` et `filiales.enregistrer` au lieu de refaire
    leur travail : le taux, le zéro sur les pertes et le remplacement d'une
    filiale déjà présente sont éprouvés là-bas, et deux calculs se répondant mal
    afficheraient des frais que le fichier d'import ne porterait pas.

    Une filiale collée deux fois — deux sélections qui se chevauchent — ne compte
    donc qu'une fois : le dernier montant gagne, comme une ressaisie, et la place
    du premier est gardée.

    Une lecture sans relevé rend une liste vide. Ce qu'on en fait est une
    décision de la page, qui refusera d'enregistrer : écrire du vide effacerait
    les relevés du jour.
    """
    filiales: list[Filiale] = []
    for releve in lecture.releves:
        filiales = enregistrer(
            filiales, calculer(releve.nom, releve.benefices, date)
        )
    return filiales
