"""Le tableau des frais de gestion par filiale, une fois par jour.

Ne touche **pas** à l'export du jeu : les relevés sont saisis à la main, et une
API en panne ne doit pas empêcher le tableau de sortir. C'est toute la raison
d'en faire une publication séparée plutôt qu'un embed de plus dans celle des
promotions.

Comme pour les promotions, l'heure et la trace de passage restent lues et écrites
là où elles vivaient avant les modules : le déménagement du mécanisme ne déplace
aucune donnée.
"""

from __future__ import annotations

import io
from typing import Any

import discord
from discord import app_commands

from src.commandes import ajouter_les_commandes_de_publication, aide_montants
from src.filiales import (
    FilialeError,
    index_de,
    nom_pour_import,
    noms_separes,
    total_frais,
    vers_import,
)
from src.modules import Envoi, Module, Publication, Tournee
from src.money import (
    TAUX_GESTION,
    MoneyError,
    format_money,
    format_money_long,
    parse_money,
)
from src.publish_filiales import embed_filiales
from src.schedule import maintenant_local


async def _preparer(bot: Any, magasin: Any, maintenant: Any) -> Tournee:
    """Un seul envoi : le tableau se lit d'un coup d'œil, il ne se découpe pas."""
    salons = await magasin.salons_filiales()
    if not salons:
        return Tournee(
            raison="aucun salon pour le tableau des frais (/filiales salon ajouter)"
        )

    # Publié même vide : l'absence de post ne se distinguerait pas d'une panne du
    # bot, et l'embed vide dit comment le remplir. C'est ce qui le distingue des
    # promotions, qui ne partent pas sans fourchette.
    filiales = await magasin.filiales()
    embed = embed_filiales(filiales, maintenant.strftime("%Y-%m-%d"))

    async def envoyer_dans(cible: Any, ephemere: bool = False) -> None:
        # `ephemeral` seulement quand il est demandé : un salon ordinaire ne
        # connaît pas cet argument et refuserait l'envoi.
        options: dict[str, Any] = {"embed": embed}
        if ephemere:
            options["ephemeral"] = True
        await cible.send(**options)

    return Tournee(
        envois=(
            Envoi(etiquette="filiales", salons=tuple(salons), envoyer=envoyer_dans),
        ),
        compte=len(filiales),
        resume=f"{len(filiales)} filiale{'s' if len(filiales) > 1 else ''}",
    )


async def _lire_heure(magasin: Any) -> str:
    return await magasin.heure_filiales()


async def _ecrire_heure(magasin: Any, heure: str) -> None:
    # `filiales_heure` et non `heure` : deux posts, deux horaires. Régler l'un en
    # déplaçant l'autre serait une surprise découverte le lendemain.
    await magasin.maj_config(filiales_heure=heure)


async def _lire_derniere(magasin: Any) -> str | None:
    return await magasin.derniere_publication_filiales()


async def _marquer(magasin: Any, date: str | None) -> None:
    await magasin.marquer_publie_filiales(date)


async def _lire_salons(magasin: Any) -> list[str]:
    return await magasin.salons_filiales()


async def _ajouter_salon(magasin: Any, salon_id: str) -> bool:
    return await magasin.ajouter_salon_filiales(salon_id)


async def _retirer_salon(magasin: Any, salon_id: str) -> bool:
    return await magasin.retirer_salon_filiales(salon_id)


PUBLICATION = Publication(
    cle="filiales",
    titre="tableau des frais",
    preparer=_preparer,
    lire_heure=_lire_heure,
    ecrire_heure=_ecrire_heure,
    lire_derniere=_lire_derniere,
    marquer=_marquer,
    lire_salons=_lire_salons,
    ajouter_salon=_ajouter_salon,
    retirer_salon=_retirer_salon,
)

async def _aujourdhui(bot: Any) -> str:
    """La date du jour dans le fuseau réglé, au format que la base retient."""
    return maintenant_local((await bot.store.config())["fuseau"]).strftime("%Y-%m-%d")


def enregistrer(bot: Any) -> None:
    """Greffe le groupe `/filiales` sur l'arbre du bot.

    Le tableau, sa saisie et son entretien au même endroit : c'est le module qui
    possède ces données, donc c'est lui qui porte les commandes qui y touchent.
    Les réglages de la publication (heure, salons, aperçu, publier) viennent du
    vocabulaire commun et ne sont pas réécrits ici.
    """
    groupe = app_commands.Group(
        name="filiales", description="Tableau des frais de gestion par filiale"
    )

    async def completer_filiale(
        interaction: discord.Interaction, saisie: str
    ) -> list[app_commands.Choice[str]]:
        """Propose les filiales déjà saisies.

        Le nom est la clé du jeu : retapé de mémoire, une faute de frappe
        créerait une **seconde** filiale au lieu de mettre la première à jour, et
        le tableau compterait deux fois la même.
        """
        debut = saisie.strip().casefold()
        return [
            app_commands.Choice(name=f.nom, value=f.nom)
            for f in await bot.store.filiales()
            if debut in f.nom.casefold()
        ][:25]  # limite Discord

    @groupe.command(name="liste", description="Filiales enregistrées et total")
    async def filiales_liste(interaction: discord.Interaction) -> None:
        filiales = await bot.store.filiales()
        await interaction.response.send_message(
            embed=embed_filiales(filiales, await _aujourdhui(bot)), ephemeral=True
        )

    @groupe.command(name="releve", description="Enregistre les bénéfices d'une filiale")
    @app_commands.describe(
        filiale="Nom de la filiale, tel qu'il est dans le jeu",
        montant="Bénéfices du cycle (ex: 2,71P, 100T)",
    )
    @app_commands.autocomplete(filiale=completer_filiale)
    async def filiales_releve(
        interaction: discord.Interaction, filiale: str, montant: str
    ) -> None:
        """Calcule les frais, enregistre le relevé et rend compte — en privé.

        Les deux cases sont obligatoires : un relevé sans montant, ou sans nom,
        n'est pas un relevé. C'est ce que l'ancienne case facultative de `/frais`
        ne pouvait pas exprimer — on ne savait pas, en tapant, si on allait
        écrire en base.

        Éphémère comme la calculatrice : les résultats de l'entreprise n'ont pas à
        s'afficher dans le salon, seul le tableau du jour est public.
        """
        try:
            valeur = parse_money(montant)
        except MoneyError as erreur:
            # Avant tout enregistrement : une filiale retenue à un montant faux
            # figurerait dans le tableau et fausserait le total.
            await interaction.response.send_message(
                f"❌ {erreur}\n{aide_montants()}", ephemeral=True
            )
            return

        existait = index_de(await bot.store.filiales(), filiale) >= 0

        try:
            releve = await bot.store.enregistrer_filiale(
                filiale, valeur, await _aujourdhui(bot)
            )
        except FilialeError as erreur:
            # Discord accepte une chaîne d'espaces : la ligne serait anonyme.
            await interaction.response.send_message(f"❌ {erreur}", ephemeral=True)
            return

        filiales = await bot.store.filiales()
        verbe = "mise à jour" if existait else "enregistrée"

        if releve.en_perte:
            # Le jeu ne rembourse pas : dit explicitement, sinon un 0 Ø se
            # lirait comme une saisie ratée.
            corps = (
                f"**{releve.nom}** {verbe} : "
                f"**{format_money(releve.benefices)}** de bénéfices, "
                f"donc **rien à payer** (en perte)."
            )
        else:
            corps = (
                f"**{releve.nom}** {verbe} : "
                f"{format_money(releve.benefices)} de bénéfices "
                f"→ **{format_money(releve.frais)}** de frais "
                f"({TAUX_GESTION.normalize():f} %).\n"
                f"-# {format_money_long(releve.frais)}"
            )

        total = total_frais(filiales)
        corps += (
            f"\nTotal des {len(filiales)} filiale{'s' if len(filiales) > 1 else ''} : "
            f"**{format_money(total)}**\n"
            f"-# {format_money_long(total)}"
        )

        if not await bot.store.salons_filiales():
            # Une saisie qui n'ira nulle part doit se voir maintenant, pas au
            # moment où l'on s'étonne de ne rien recevoir.
            corps += "\n⚠️ Aucun salon pour le tableau : `/filiales salon ajouter`."

        await interaction.response.send_message(corps, ephemeral=True)

    @groupe.command(name="retirer", description="Oublie une filiale, un lot, ou tout")
    @app_commands.describe(
        filiales="Un nom, plusieurs séparés par des virgules, ou `tout`",
        confirmer="Obligatoire pour un lot ou pour `tout` : le retrait est définitif",
    )
    @app_commands.autocomplete(filiales=completer_filiale)
    async def filiales_retirer(
        interaction: discord.Interaction, filiales: str, confirmer: bool = False
    ) -> None:
        """Une seule commande là où il y en avait deux.

        `retirer` et `retirer-plusieurs` faisaient le même geste, l'une refusant
        ce que l'autre acceptait : il fallait savoir laquelle prendre avant de
        savoir combien de noms on allait donner.

        Discord n'offre pas de champ répétable : les noms arrivent donc dans une
        chaîne, découpée par `noms_separes`. `tout` vide la liste — mais une saisie
        **vide** ne vaut jamais `tout` : ce serait la pire lecture d'un accident.
        """
        saisie = filiales.strip()

        if not saisie:
            await interaction.response.send_message(
                "❌ Aucun nom saisi. Donne un nom, plusieurs séparés par des "
                "virgules, ou `tout` pour vider la liste.",
                ephemeral=True,
            )
            return

        connues = await bot.store.filiales()
        tout = saisie.casefold() == "tout"
        noms = [f.nom for f in connues] if tout else noms_separes(saisie)

        if not noms:
            await interaction.response.send_message(
                "ℹ️ Aucune filiale enregistrée : rien à retirer.\n"
                "-# `/filiales releve` pour en ajouter une.",
                ephemeral=True,
            )
            return

        # La case ne protège que ce qui la mérite : plus d'un nom, ou `tout`, qui
        # ne nomme pas ce qu'il emporte. Une cérémonie sur un geste d'un mot
        # apprendrait à cocher sans lire, et la case ne protégerait plus le lot
        # qu'elle est là pour protéger.
        if (tout or len(noms) > 1) and not confirmer:
            # Ce qui va partir, nommément : « 12 filiales » ne permettrait pas de
            # voir qu'on s'est trompé de lot avant de le perdre.
            liste = ", ".join(f"`{n}`" for n in noms[:15])
            if len(noms) > 15:
                liste += f" … (+{len(noms) - 15})"
            await interaction.response.send_message(
                f"❌ Rien retiré : coche `confirmer` pour aller au bout.\n"
                f"-# {len(noms)} filiale(s) visée(s) : {liste}",
                ephemeral=True,
            )
            return

        retirees, inconnus = await bot.store.retirer_filiales(noms)

        if not retirees:
            # Les connues listées : sinon on ne sait pas si c'est une faute de
            # frappe ou une filiale jamais saisie.
            noms_connus = [f.nom for f in connues]
            liste = ", ".join(f"`{n}`" for n in noms_connus) if noms_connus else "*aucune*"
            manques = ", ".join(f"« {n} »" for n in inconnus)
            await interaction.response.send_message(
                f"❌ Aucune filiale nommée {manques}. Filiales : {liste}.",
                ephemeral=True,
            )
            return

        restantes = await bot.store.filiales()
        message = (
            f"✅ {retirees} filiale(s) retirée(s) du tableau.\n"
            f"-# Reste {len(restantes)} filiale(s), "
            f"{format_money(total_frais(restantes))} de frais."
        )
        if inconnus:
            # Dits, sinon on croirait ces filiales supprimées alors qu'elles
            # reviendront dans le tableau du soir.
            message += "\n-# Inconnues, donc laissées : " + ", ".join(
                f"`{n}`" for n in inconnus
            )

        await interaction.response.send_message(message, ephemeral=True)

    @groupe.command(name="vider", description="Remet tous les bénéfices à 0, garde les noms")
    @app_commands.describe(confirmer="À cocher : les relevés du cycle sont effacés")
    async def filiales_vider(
        interaction: discord.Interaction, confirmer: bool
    ) -> None:
        """Ouvre un nouveau cycle sans perdre les noms.

        Vide les **montants**, pas la liste : les noms sont la clé d'import du jeu
        et l'assise de l'autocomplétion, si bien qu'un nouveau cycle ne demande
        que de ressaisir les montants. Pour perdre les noms aussi, c'est
        `/filiales retirer tout`.

        À zéro, chaque filiale s'affiche « en perte » dans le tableau, ce qui est
        exact — il n'y a rien à prélever.
        """
        filiales = await bot.store.filiales()

        if not filiales:
            await interaction.response.send_message(
                "ℹ️ Aucune filiale enregistrée : rien à vider.\n"
                "-# `/filiales releve` pour en ajouter une.",
                ephemeral=True,
            )
            return

        if not confirmer:
            await interaction.response.send_message(
                f"❌ Rien effacé : coche `confirmer` pour aller au bout.\n"
                f"-# {len(filiales)} relevé(s) seraient remis à 0 Ø, "
                f"les noms étant gardés.",
                ephemeral=True,
            )
            return

        combien = await bot.store.remettre_a_zero_filiales(await _aujourdhui(bot))

        await interaction.response.send_message(
            f"✅ {combien} filiale(s) remise(s) à 0 Ø, noms gardés.\n"
            f"-# `/filiales releve` pour saisir le nouveau cycle ; "
            f"l'autocomplétion propose toujours les noms.",
            ephemeral=True,
        )

    @groupe.command(
        name="export", description="Le tableau au format d'import du jeu (.txt)"
    )
    async def filiales_export(interaction: discord.Interaction) -> None:
        """Le fichier à donner au champ d'import du jeu.

        En pièce jointe et non dans un bloc de code : la tabulation ne se saisit
        pas dans Discord — la touche y sert à l'autocomplétion — et Discord
        normalise les fins de ligne du contenu d'un message, si bien qu'un bloc
        ne pourrait pas porter le CRLF que le jeu attend. Une pièce jointe n'est
        pas rendue : les octets arrivent tels qu'ils ont été écrits.
        """
        filiales = await bot.store.filiales()
        if not filiales:
            # Pas de fichier vide : un `.txt` de zéro octet se lirait comme une
            # panne du bot plutôt que comme un tableau vide.
            await interaction.response.send_message(
                "ℹ️ Aucune filiale enregistrée : rien à exporter.\n"
                "-# `/filiales releve` pour remplir le tableau.",
                ephemeral=True,
            )
            return

        contenu = vers_import(filiales)
        # Daté : deux exports d'affilée porteraient sinon le même nom dans le fil,
        # et on ne saurait plus lequel est à jour.
        fichier = discord.File(
            fp=io.BytesIO(contenu.encode("utf-8")),
            filename=f"frais-{await _aujourdhui(bot)}.txt",
        )

        # Un nom peut porter une tabulation ou un retour à la ligne **collés** :
        # neutralisés, ils ne casseront pas le format, mais le nom ne sera plus
        # celui du jeu. Le dire, sans quoi rien n'expliquerait le refus à l'import.
        deformes = [
            nom_pour_import(f.nom) for f in filiales if nom_pour_import(f.nom) != f.nom
        ]
        avertissement = (
            "\n-# ⚠️ Nom(s) réécrit(s) faute de tenir sur une ligne : "
            + ", ".join(f"`{nom}`" for nom in deformes)
            if deformes
            else ""
        )

        await interaction.response.send_message(
            f"📄 {len(filiales)} ligne(s) au format d'import du jeu.\n"
            f"-# Une tabulation et un CRLF par ligne : à téléverser ou à copier tel "
            f"quel — le format ne survivrait pas à un message Discord."
            f"{avertissement}",
            file=fichier,
            ephemeral=True,
        )

    ajouter_les_commandes_de_publication(groupe, bot, PUBLICATION)
    bot.tree.add_command(groupe)


MODULE = Module(
    nom="filiales",
    titre="Tableau des frais",
    description="Relevés de frais de gestion par filiale, et tableau quotidien.",
    ordre=30,
    enregistrer=enregistrer,
    publications=(PUBLICATION,),
)
