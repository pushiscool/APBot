import nextcord
import re
from nextcord import slash_command, Permissions, Interaction, User, Embed, Member, TextChannel, Object, Color
from nextcord.ext import commands
from typing import Optional
from bot_base import APBot
from app_config import get_command_guild_ids, load_optional_config
from datetime import datetime, timedelta, timezone

conf = load_optional_config()
COMMAND_GUILD_IDS = get_command_guild_ids(conf)


def to_snowflake(value):
    if value is None:
        return None
    if hasattr(value, "id"):
        value = value.id
    if isinstance(value, int):
        value = str(value)
    if isinstance(value, str):
        value = value.strip()
        match = re.search(r"(?<!\d)\d{17,20}(?!\d)", value)
        if match:
            return int(match.group(0))
    return None


class Infraction(commands.Cog):
    def __init__(self, bot: APBot) -> None:
        self.bot = bot

    def has_mod_role(self, member: Member) -> bool:
        allowed_role_names = {"Trial Chat Moderator", "Chat Moderator", "Admin"}
        allowed_role_ids = {
            role_id
            for role_id in (
                conf.get("bot_staff_role_id"),
                conf.get("special_perms_role_id"),
            )
            if role_id is not None
        }

        perms = getattr(member, "guild_permissions", None)

        if getattr(perms, "administrator", False):
            return True

        if getattr(perms, "moderate_members", False):
            return True

        for role in member.roles:
            if role.name in allowed_role_names:
                return True
            if getattr(role, "id", None) in allowed_role_ids:
                return True

        return False

    async def get_moderator_line(self, inter: Interaction, raw_mod):
        moderator_id = to_snowflake(raw_mod)

        mod = None
        if moderator_id and inter.guild is not None:
            mod = inter.guild.get_member(moderator_id)
            if mod is None:
                try:
                    mod = await inter.guild.fetch_member(moderator_id)
                except nextcord.HTTPException:
                    mod = None

        if mod is None and moderator_id:
            try:
                mod = await self.bot.fetch_user(moderator_id)
            except nextcord.HTTPException:
                mod = None

        if mod:
            display = (
                getattr(mod, "display_name", None)
                or getattr(mod, "global_name", None)
                or getattr(mod, "name", None)
                or str(moderator_id)
            )
            return f"{mod.mention} ({display})"

        if moderator_id:
            return f"<@{moderator_id}> (unknown)"

        return f"{raw_mod} (unknown)" if raw_mod else "Unknown moderator"

    @slash_command(
        name="warnings",
        description="Show infraction history of a member.",
        guild_ids=COMMAND_GUILD_IDS,
        default_member_permissions=Permissions(moderate_members=True),
    )
    async def warnings(self, inter: Interaction, member: Member):
        if not self.has_mod_role(inter.user):
            await inter.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return

        await inter.response.defer(with_message=True)

        infractions = await self.bot.db.base_db.get_user_infractions(member.id)
        inf_points = await self.bot.db.base_db.add_inf_points(member.id, 0)

        fetching_msg = await inter.original_message()

        if not infractions:
            await fetching_msg.edit(content=f"{member.mention} has no infractions.")
            return

        await fetching_msg.edit(content=f"Fetching {member.mention}'s warnings...")

        color_map = {
            "warn": self.bot.colors.get("yellow", Color.yellow()),
            "mute": self.bot.colors.get("orange", Color.orange()),
            "pseudo-mute": self.bot.colors.get("light_orange", Color.orange()),
            "kick": self.bot.colors.get("dark_orange", Color.orange()),
            "ban": self.bot.colors.get("red", Color.red()),
            "force-ban": self.bot.colors.get("red", Color.red()),
            "unmute": self.bot.colors.get("green", Color.green()),
            "unban": self.bot.colors.get("green", Color.green()),
            "note": Color.purple(),
        }

        total = len(infractions)

        for index, inf in enumerate(infractions, start=1):
            action = (
                "Internal Note"
                if inf.actiontype == "note"
                else (inf.actiontype or "unknown").capitalize().replace("-", " ")
            )

            reason = inf.reason or "No reason provided"
            mod_line = await self.get_moderator_line(inter, getattr(inf, "moderator", None))

            time_val = getattr(inf, "actiontime", None)

            if isinstance(time_val, str):
                try:
                    time_val = datetime.fromisoformat(time_val.replace("Z", "+00:00"))
                except ValueError:
                    time_val = datetime.now(timezone.utc)

            if not isinstance(time_val, datetime):
                time_val = datetime.now(timezone.utc)

            if time_val.tzinfo is None:
                time_val = time_val.replace(tzinfo=timezone.utc)

            unix = int(time_val.timestamp())
            timestamp = f"<t:{unix}:F> (<t:{unix}:R>)"

            duration_line = ""
            if getattr(inf, "duration", None):
                try:
                    dur = inf.duration
                    duration_seconds = int(dur.total_seconds()) if isinstance(dur, timedelta) else int(dur)

                    days, rem = divmod(duration_seconds, 86400)
                    hours, rem = divmod(rem, 3600)
                    minutes, seconds = divmod(rem, 60)

                    parts = []
                    if days:
                        parts.append(f"{days}d")
                    if hours:
                        parts.append(f"{hours}h")
                    if minutes:
                        parts.append(f"{minutes}m")
                    if seconds:
                        parts.append(f"{seconds}s")

                    duration_line = f"Duration: {' '.join(parts) if parts else '0s'}\n"
                except (ValueError, TypeError, AttributeError):
                    pass

            embed = Embed(
                title=action,
                description=(
                    f"Reason: {reason}\n"
                    f"{duration_line}"
                    f"Responsible Mod: {mod_line}\n"
                    f"{index}/{total} infractions • {timestamp}"
                ),
                color=color_map.get(getattr(inf, "actiontype", ""), Color.gold()),
            )

            await inter.channel.send(embed=embed)

        await fetching_msg.edit(
            content=f"Complete, all infractions shown! {member.mention} has `{inf_points}` infraction point(s)."
        )

    @slash_command(
        name="editip",
        description="Edit a member's infraction points.",
        default_member_permissions=Permissions(moderate_members=True),
        guild_ids=COMMAND_GUILD_IDS,
    )
    async def editip(self, interaction: Interaction, member: Member, change: int):
        if not self.has_mod_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        new_inf_points = await self.bot.db.base_db.add_inf_points(member.id, change)

        await interaction.response.send_message(
            f"Added {change} to {member.mention}'s infraction points, they now have {new_inf_points} total."
        )

        if new_inf_points < 30:
            return

        ch: Optional[TextChannel] = await self.bot.getch_channel(self.bot.config.get("ban_review_channel-id"))
        if ch:
            await ch.send(
                content=f"<@&{self.bot.config.get('mod_role_id')}>",
                embed=Embed(
                    title="Member has reached 30 inf points",
                    description=f"{member.mention} has {new_inf_points} IPs and should be reviewed for a ban.",
                    color=self.bot.colors.get("red"),
                ),
            )

        updates_channel = nextcord.utils.get(interaction.guild.text_channels, name="important-updates")
        if updates_channel:
            mod_role = nextcord.utils.get(interaction.guild.roles, name="Chat Moderator")
            await updates_channel.send(
                content=mod_role.mention if mod_role else None,
                embed=Embed(
                    title="Member has reached 30 infraction points!",
                    description=f"{member.mention} has `{new_inf_points}` infraction points and should be reviewed for a ban.",
                    color=self.bot.colors.get("red"),
                ),
            )

    @slash_command(
        name="userip",
        description="View a member's infraction points.",
        default_member_permissions=Permissions(moderate_members=True),
        guild_ids=COMMAND_GUILD_IDS,
    )
    async def userip(self, interaction: Interaction, member: Member):
        if not self.has_mod_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        inf_points = await self.bot.db.base_db.add_inf_points(member.id, 0)

        if inf_points == 0:
            await interaction.response.send_message(f"{member.mention} has no infraction points.")
        elif inf_points == 1:
            await interaction.response.send_message(f"{member.mention} has 1 infraction point.")
        else:
            await interaction.response.send_message(f"{member.mention} has {inf_points} infraction points.")

    @slash_command(name="infpoints", description="View how many infraction points you have.", guild_ids=COMMAND_GUILD_IDS)
    async def infpoints(self, interaction: Interaction):
        target_id = interaction.user.id
        inf_points = await self.bot.db.base_db.add_inf_points(target_id, 0)

        if inf_points == 0:
            await interaction.response.send_message("You have no infraction points.", ephemeral=True)
        elif inf_points == 1:
            await interaction.response.send_message("You have 1 infraction point.", ephemeral=True)
        else:
            await interaction.response.send_message(f"You have {inf_points} infraction points.", ephemeral=True)


def setup(bot: APBot) -> None:
    bot.add_cog(Infraction(bot))