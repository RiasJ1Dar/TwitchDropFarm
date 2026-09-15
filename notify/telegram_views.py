"""Telegram Views: форматування повідомлень, HTML-шаблони та клавіатури.

Відокремлює шар відображення (презентації) від координації подій та мережевого транспорту.
Усі тексти використовують локалізацію t() та безпечне екранування html.escape().
"""
from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.i18n import t
from core.toolbox import plural

if TYPE_CHECKING:
    from core.miner import Miner as Twitch
    from core.model import Campaign


def command_catalog() -> tuple[tuple[str, str, str], ...]:
    """Єдине джерело правди для команд: меню Telegram і /help збираються звідси."""
    return (
        ("status", t("tg_cmd_status"), ""),
        ("inventory", t("tg_cmd_inventory"), ""),
        ("campaigns", t("tg_cmd_campaigns"), ""),
        ("pause", t("tg_cmd_pause"), ""),
        ("resume", t("tg_cmd_resume"), ""),
        ("switch", t("tg_cmd_switch"), t("tg_arg_channel")),
        ("priority", t("tg_cmd_priority"), t("tg_arg_game")),
        ("watch", t("tg_cmd_watch"), t("tg_arg_game")),
        ("report", t("tg_cmd_report"), t("tg_arg_days")),
        ("export", t("tg_cmd_export"), ""),
        ("update", t("tg_cmd_update"), ""),
        ("reload", t("tg_cmd_reload"), ""),
        ("hide", t("tg_cmd_hide"), ""),
        ("show", t("tg_cmd_show"), ""),
        ("reboot", t("tg_cmd_reboot"), ""),
        ("menu", t("tg_cmd_menu"), ""),
        ("help", t("tg_cmd_help"), ""),
    )


def help_text() -> str:
    """Текст довідки /help з усіма доступними командами та аргументами."""
    return "<b>" + t("tg_help_title") + "</b>\n" + "\n".join(
        f"/{name}{args} — {description}"
        for name, description, args in command_catalog()
    )


def control_buttons() -> tuple[tuple[tuple[str, str], ...], ...]:
    """Розкладка кнопок головного меню керування."""
    return (
        ((t("tg_btn_status"), "status"), (t("tg_btn_inventory"), "inventory")),
        ((t("tg_btn_campaigns"), "campaigns"), (t("tg_btn_report"), "report")),
        ((t("tg_btn_pause"), "pause"), (t("tg_btn_resume"), "resume")),
        ((t("tg_btn_hide"), "hide"), (t("tg_btn_show"), "show")),
        ((t("tg_btn_reload"), "reload"), (t("tg_btn_reboot"), "reboot")),
        ((t("tg_btn_export"), "export"), (t("tg_btn_help"), "help")),
    )


def button_commands() -> dict[str, str]:
    """Словник відповідності підпису кнопки на панелі до назви команди."""
    return {label: command for row in control_buttons() for label, command in row}


def control_keyboard() -> dict[str, Any]:
    """Клавіатура під полем вводу — постійна панель керування."""
    return {
        "keyboard": [
            [{"text": label} for label, _command in row]
            for row in control_buttons()
        ],
        "resize_keyboard": True,   # кнопки по висоті тексту, а не на пів екрана
        "is_persistent": True,     # не ховати після натискання
        "input_field_placeholder": t("tg_placeholder"),
    }


def update_buttons() -> dict[str, Any]:
    """Інлайн-кнопки для сповіщення про доступне оновлення."""
    return {
        "inline_keyboard": [[
            {"text": t("tg_btn_update"), "callback_data": "update"},
            {"text": t("tg_btn_later"), "callback_data": "later"},
        ]],
    }


# ================================================================ відображення станів


def render_status(twitch: Twitch) -> str:
    """Форматування повної відповіді на /status."""
    esc = html.escape
    lines = [t("tg_status_title")]
    channel = twitch.watching.peek()
    if twitch._paused:
        lines.append(t("tg_status_paused"))
    elif channel is None:
        lines.append(t("tg_status_idle"))
    else:
        game = channel.game.name if channel.game is not None else "—"
        lines.append(t("tg_status_channel",
                       channel=esc(channel.name), game=esc(game)))
    campaign = twitch.active_campaign()
    if campaign is not None:
        drop = campaign.next_drop
        if drop is not None:
            lines.append(t(
                "tg_status_drop",
                name=esc(drop.name),
                minutes=drop.minutes,
                required=drop.required_minutes,
                left=drop.minutes_left,
            ))
        lines.append(t(
            "tg_status_campaign",
            name=esc(campaign.name),
            taken=campaign.taken_count,
            total=campaign.total,
        ))
    active = [c for c in twitch.campaigns
              if c.running and c.available_to_me and not c.everything_taken]
    lines.append(t("tg_status_queue", count=len(active)))
    if twitch.api.offline:
        lines.append(t("tg_status_offline"))
    return "\n".join(lines)


def render_inventory(campaigns: Iterable[Campaign]) -> str:
    """Форматування відповіді на /inventory."""
    esc = html.escape
    lines = [t("tg_inv_title")]
    shown = 0
    for campaign in campaigns:
        if not campaign.available_to_me or campaign.everything_taken or not campaign.running:
            continue
        lines.append(f"\n<b>{esc(campaign.game.name)}</b> — {esc(campaign.name)}")
        for drop in campaign.all_drops:
            mark = "✅" if drop.taken else "▫️"
            lines.append(t(
                "tg_inv_drop",
                mark=mark,
                name=esc(drop.name),
                minutes=drop.minutes,
                required=drop.required_minutes,
            ))
        shown += 1
        if shown >= 10:
            lines.append(t("tg_inv_more"))
            break
    if shown == 0:
        lines.append(t("tg_inv_empty"))
    return "\n".join(lines)


def render_campaigns(campaigns: Sequence[Campaign]) -> str:
    """Форматування відповіді на /campaigns."""
    esc = html.escape
    lines = [t("tg_camp_title")]
    now = datetime.now(timezone.utc)
    for campaign in campaigns[:25]:
        if campaign.over:
            state = t("tg_camp_past")
        elif campaign.not_started:
            state = t("tg_camp_soon")
        elif not campaign.available_to_me:
            state = t("tg_camp_unlinked")
        elif campaign.everything_taken:
            state = t("tg_camp_done")
        else:
            hours = max(0, int((campaign.closes_at - now).total_seconds() // 3600))
            state = t("tg_camp_active", hours=hours)
        lines.append(t(
            "tg_camp_line",
            game=esc(campaign.game.name),
            taken=campaign.taken_count,
            total=campaign.total,
            state=state,
        ))
    if not campaigns:
        lines.append(t("tg_camp_unread"))
    return "\n".join(lines)


def render_priority(current: Sequence[str]) -> str:
    """Форматування списку пріоритетів ігор."""
    listing = "\n".join(f"{i + 1}. {html.escape(g)}"
                        for i, g in enumerate(current)) or t("tg_empty")
    return f"{t('tg_priority_title')}\n{listing}\n\n{t('tg_priority_hint')}"


def render_watch(current: Sequence[str]) -> str:
    """Форматування списку спостереження за іграми."""
    listing = "\n".join(f"{i + 1}. {html.escape(g)}"
                        for i, g in enumerate(current)) or t("tg_empty")
    return (
        f"{t('tg_watch_title')}\n{listing}\n\n"
        f"{t('tg_watch_body')}\n{t('tg_watch_hint')}"
    )


def render_report(summary_text: str) -> str:
    """Форматування звіту /report."""
    body = html.escape(summary_text)
    return f"{t('tg_report_title')}\n<pre>{body}</pre>"


def render_export_success(paths: Sequence[Path]) -> str:
    """Форматування успішного експорту зі списком створених файлів."""
    listing = "\n".join(str(path) for path in paths)
    return f"{t('tg_export_title')}\n<code>{html.escape(listing)}</code>"


def render_export_error(error: str) -> str:
    """Форматування помилки під час експорту."""
    return t("tg_export_fail", error=html.escape(error))


# ================================================================ допоміжні форматери подій


def format_link_hint(campaigns: Sequence[Any]) -> str:
    """Форматування підказки про необхідність прив'язки акаунта."""
    lines: list[str] = []
    for item in campaigns[:5]:
        line = f"• {html.escape(item.name)} ({html.escape(item.game)})"
        url = getattr(item, "url", "")
        if url:
            line += "\n" + html.escape(str(url))
        lines.append(line)
    return t("tg_link_hint", names="\n".join(lines))


def format_new_campaigns(campaigns: Sequence[Any]) -> str:
    """Форматування сповіщення про появу нових кампаній."""
    news = [t("tg_new_campaign_title", count=len(campaigns))]
    for fresh in campaigns[:5]:
        ends = fresh.ends_at.astimezone().strftime("%d.%m %H:%M")
        news.append(t(
            "tg_new_campaign_item",
            name=html.escape(fresh.name.strip()),
            game=html.escape(fresh.game),
            drops=fresh.total_drops,
            unit=plural(
                fresh.total_drops,
                t("tg_drop_one"), t("tg_drop_few"), t("tg_drop_many"),
            ),
            ends=ends,
        ))
    if len(campaigns) > 5:
        news.append(t("tg_new_campaign_more", n=len(campaigns) - 5))
    news.append(t("tg_new_campaign_hint"))
    return "\n".join(news)


def format_deadline_risk(campaigns: Sequence[Any]) -> str:
    """Форматування сповіщення про ризик прострочення дедлайну кампаній."""
    lines = [t("tg_deadline_title", count=len(campaigns))]
    for item in campaigns[:5]:
        lines.append(t(
            "tg_deadline_item",
            name=html.escape(item.name),
            game=html.escape(item.game),
            needed=item.minutes_needed,
            available=item.minutes_available,
        ))
    if len(campaigns) > 5:
        lines.append(t("tg_deadline_more", n=len(campaigns) - 5))
    return "\n".join(lines)
