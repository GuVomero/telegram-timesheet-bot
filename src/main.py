from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import unicodedata
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CallbackContext, CommandHandler, ContextTypes

from .report import build_month_hours_summary, build_month_report, build_month_report_images
from .storage import (
    add_punch,
    delete_punches_between,
    get_last_punch,
    init_db,
    list_daily_targets_between,
    list_daily_targets_for_month,
    list_known_users,
    list_punches_between,
    list_work_modes_for_month,
    set_daily_target,
    set_work_mode,
)


logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
ACTION_LABELS = {
    "ENTRY": "ENTRADA",
    "LUNCH_OUT": "SAIDA ALMOCO",
    "RETURN": "ENTRADA 2",
    "EXIT": "SAIDA",
    # Compatibilidade com eventos legados.
    "IN": "ENTRADA",
    "OUT": "SAIDA",
}
MANUAL_ACTIONS = {
    "entrada": "ENTRY",
    "almoco": "LUNCH_OUT",
    "entrada_2": "RETURN",
    "entrada2": "RETURN",
    "retorno": "RETURN",
    "saida": "EXIT",
}
FIXED_USERS_ENV = "FIXED_USERS"
TIME_TOKEN_RE = re.compile(r"^\d{1,2}:\d{2}$")
WORK_MODE_TOKENS = {
    "home": "HOME",
    "homeoffice": "HOME",
    "campo": "CAMPO",
    "padrao": "STANDARD",
    "default": "STANDARD",
}
CORRECTION_ORDER: list[tuple[str, str]] = [
    ("ENTRY", "entrada"),
    ("LUNCH_OUT", "almoco"),
    ("RETURN", "entrada_2"),
    ("EXIT", "saida"),
]


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Variavel de ambiente obrigatoria ausente: {name}")
    return value


def _load_fixed_users_from_env() -> dict[str, tuple[int, str]]:
    # Formato:
    # FIXED_USERS="eu=123|Seu Nome;colega=456|Nome Colega;amigo=456|Nome Colega"
    raw = os.getenv(FIXED_USERS_ENV, "").strip()
    if not raw:
        return {}

    out: dict[str, tuple[int, str]] = {}
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item or "|" not in item:
            logger.warning("Entrada invalida em FIXED_USERS ignorada: %s", item)
            continue
        alias, rest = item.split("=", 1)
        user_id_raw, user_name = rest.split("|", 1)
        alias_norm = _normalize_text(alias)
        if not alias_norm:
            continue
        try:
            user_id = int(user_id_raw.strip())
        except ValueError:
            logger.warning("ID invalido em FIXED_USERS ignorado: %s", item)
            continue
        user_name = user_name.strip()
        if not user_name:
            logger.warning("Nome vazio em FIXED_USERS ignorado: %s", item)
            continue
        out[alias_norm] = (user_id, user_name)
    return out


def _parse_hhmm_duration(raw: str) -> timedelta | None:
    token = raw.strip()
    if not TIME_TOKEN_RE.fullmatch(token):
        return None
    hour_str, minute_str = token.split(":", 1)
    hour = int(hour_str)
    minute = int(minute_str)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return timedelta(hours=hour, minutes=minute)


def _load_daily_target_overrides_by_user(
    chat_id: int,
    year: int,
    month: int,
    targets: list[tuple[int, str]] | None = None,
) -> dict[int, dict[date, timedelta]]:
    target_ids = {user_id for user_id, _ in targets} if targets is not None else None
    rows = list_daily_targets_for_month(chat_id, year, month, user_ids=target_ids)
    out: dict[int, dict[date, timedelta]] = defaultdict(dict)
    for row in rows:
        out[row.user_id][row.target_day] = row.target
    return dict(out)


def _load_work_modes_by_user(
    chat_id: int,
    year: int,
    month: int,
    targets: list[tuple[int, str]] | None = None,
) -> dict[int, dict[date, str]]:
    target_ids = {user_id for user_id, _ in targets} if targets is not None else None
    rows = list_work_modes_for_month(chat_id, year, month, user_ids=target_ids)
    out: dict[int, dict[date, str]] = defaultdict(dict)
    for row in rows:
        out[row.user_id][row.target_day] = row.mode
    return dict(out)


def _display_name(update: Update) -> str:
    user = update.effective_user
    if user is None:
        return "desconhecido"
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full or (user.username or str(user.id))


def _is_allowed_chat(update: Update, target_chat_id: int) -> bool:
    msg = update.effective_message
    chat = update.effective_chat
    if msg is None or chat is None:
        return False

    if chat.id != target_chat_id:
        return False
    return True


async def _ensure_allowed_chat_or_reply(update: Update, target_chat_id: int) -> bool:
    if _is_allowed_chat(update, target_chat_id):
        return True
    msg = update.effective_message
    chat = update.effective_chat
    if msg is not None and chat is not None:
        await msg.reply_text(
            f"Este bot esta configurado para outro chat. Atual: {chat.id} | Esperado: {target_chat_id}"
        )
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    await update.effective_message.reply_text(
        "Bot de ponto ativo.\n"
        "Use /help para ver os comandos e formatos de uso."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    assert update.effective_message
    await update.effective_message.reply_text(
        "Guia rapido:\n\n"
        "[Padroes de uso]\n"
        "- [usuarios] e opcional em comandos que alteram dados.\n"
        "- Sem [usuarios], o comando aplica em voce.\n"
        "- <data> e opcional quando indicado.\n"
        "- Formatos de data: YYYY-MM-DD ou DD/MM/YYYY.\n"
        "- Formato de hora/base: HH:MM.\n\n"
        "[Registro de ponto]\n"
        "/entrada [usuarios]\n"
        "/almoco [usuarios]\n"
        "/entrada_2 [usuarios]\n"
        "/saida [usuarios]\n"
        "/status\n\n"
        "[Ajustes e manutencao]\n"
        "/clear [data] [usuarios]\n"
        "/time_base <HH:MM> [usuarios]\n"
        "/time_base <data> <HH:MM> [usuarios]\n"
        "/workmode <home|campo|padrao> [usuarios]\n"
        "/workmode <data> <home|campo|padrao> [usuarios]\n\n"
        "[Correcao manual]\n"
        "/corrigir <tipo> <HH:MM> [usuarios]\n"
        "/corrigir <data> <tipo> <HH:MM> [usuarios]\n"
        "/corrigir <h1> <h2> <h3> <h4> [usuarios]\n"
        "/corrigir <data> <h1> <h2> <h3> <h4> [usuarios]\n"
        "Tipos: entrada, almoco, entrada_2, saida.\n"
        "No bloco use '-' para manter um horario sem alteracao.\n\n"
        "[Relatorios]\n"
        "/mes [mes|data] [usuarios]\n"
        "/mes_png [mes|data] [usuarios]\n"
        "/resumo [mes|data] [usuarios]\n"
        "Formato de mes: YYYY-MM ou MM/YYYY.\n\n"
        "[Regras da jornada]\n"
        "- entrada -> almoco\n"
        "- entrada_2 -> saida\n"
        "- entrada -> almoco -> entrada_2 -> saida\n\n"
        "[Utilitarios]\n"
        "/chat_id"
    )


async def chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    assert update.effective_chat and update.effective_message
    await update.effective_message.reply_text(f"Chat ID deste grupo: {update.effective_chat.id}")


async def entrada(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    await _register_action_for_targets(
        update=update,
        context=context,
        action="ENTRY",
        allowed_previous={None, "EXIT", "OUT", "LUNCH_OUT"},
        error_message="Sequencia invalida. Finalize a etapa atual antes de nova /entrada.",
    )


async def almoco(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    await _register_action_for_targets(
        update=update,
        context=context,
        action="LUNCH_OUT",
        allowed_previous={"ENTRY", "IN"},
        error_message="Para /almoco, registre /entrada antes.",
    )


async def entrada_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    await _register_action_for_targets(
        update=update,
        context=context,
        action="RETURN",
        allowed_previous={None, "LUNCH_OUT", "OUT", "EXIT"},
        error_message="Para /entrada_2, finalize o periodo anterior antes.",
    )


async def saida(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    await _register_action_for_targets(
        update=update,
        context=context,
        action="EXIT",
        allowed_previous={"RETURN"},
        error_message="Para /saida, registre /entrada_2 antes.",
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message
    assert chat and user and msg

    last = get_last_punch(chat.id, user.id)
    if last is None:
        await msg.reply_text("Sem registros ainda.")
        return

    tz: ZoneInfo = context.bot_data["tz"]
    kind = ACTION_LABELS.get(last.action, last.action)
    await msg.reply_text(f"Ultimo registro: {kind} em {last.ts_utc.astimezone(tz):%d/%m/%Y %H:%M:%S}")


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    chat = update.effective_chat
    sender = update.effective_user
    msg = update.effective_message
    assert chat and sender and msg

    tz: ZoneInfo = context.bot_data["tz"]
    target_day = datetime.now(tz).date()
    user_args = context.args

    if context.args:
        parsed_day = _parse_date_input(context.args[0].strip())
        if parsed_day is not None:
            target_day = parsed_day
            user_args = context.args[1:]

    start_utc, end_utc = _day_window_utc(target_day, tz)

    sender_name = _display_name(update)
    targets, err = _resolve_target_users(
        chat.id,
        sender.id,
        sender_name,
        user_args,
        context.bot_data.get("fixed_users"),
    )
    if err:
        await msg.reply_text(err)
        return

    lines: list[str] = []
    for target_id, target_name in targets:
        deleted = delete_punches_between(
            chat_id=chat.id,
            user_id=target_id,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        if deleted == 0:
            lines.append(f"ERRO {target_name}: nenhum registro encontrado em {target_day:%d/%m/%Y}.")
        else:
            lines.append(f"OK {target_name}: registros apagados de {target_day:%d/%m/%Y}: {deleted}.")

    await msg.reply_text("\n".join(lines))


async def time_base(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    chat = update.effective_chat
    sender = update.effective_user
    msg = update.effective_message
    assert chat and sender and msg

    if not context.args:
        await msg.reply_text(
            "Uso: /time_base <HH:MM> [usuarios]\n"
            "ou: /time_base <data> <HH:MM> [usuarios]\n"
            "Exemplo feriado: /time_base 2026-04-21 00:00 gustavo"
        )
        return

    tz: ZoneInfo = context.bot_data["tz"]
    target_day = datetime.now(tz).date()
    payload = context.args

    parsed_day = _parse_date_input(context.args[0].strip())
    if parsed_day is not None:
        target_day = parsed_day
        payload = context.args[1:]

    if not payload:
        await msg.reply_text("Informe a base no formato HH:MM. Ex.: /time_base 08:00")
        return

    target = _parse_hhmm_duration(payload[0])
    if target is None:
        await msg.reply_text("Valor invalido para base. Use formato HH:MM (ex.: 08:00 ou 00:00).")
        return

    user_args = payload[1:]
    sender_name = _display_name(update)
    targets, err = _resolve_target_users(
        chat.id,
        sender.id,
        sender_name,
        user_args,
        context.bot_data.get("fixed_users"),
    )
    if err:
        await msg.reply_text(err)
        return

    target_text = f"{int(target.total_seconds() // 3600):02d}:{int((target.total_seconds() // 60) % 60):02d}"
    lines: list[str] = []
    for target_id, target_name in targets:
        set_daily_target(chat.id, target_id, target_name, target_day, target)
        lines.append(f"OK {target_name}: time_base de {target_day:%d/%m/%Y} definido para {target_text}.")

    await msg.reply_text("\n".join(lines))


async def workmode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    chat = update.effective_chat
    sender = update.effective_user
    msg = update.effective_message
    assert chat and sender and msg

    if not context.args:
        await msg.reply_text(
            "Uso: /workmode <home|campo> [usuarios]\n"
            "ou: /workmode <data> <home|campo> [usuarios]\n"
            "Exemplo: /workmode 2026-04-21 campo gustavo"
        )
        return

    tz: ZoneInfo = context.bot_data["tz"]
    target_day = datetime.now(tz).date()
    payload = context.args

    parsed_day = _parse_date_input(context.args[0].strip())
    if parsed_day is not None:
        target_day = parsed_day
        payload = context.args[1:]

    if not payload:
        await msg.reply_text("Informe um modo: home ou campo.")
        return

    mode_token = _normalize_text(payload[0]).replace(" ", "")
    mode = WORK_MODE_TOKENS.get(mode_token)
    if mode is None:
        await msg.reply_text("Modo invalido. Use: home ou campo.")
        return

    user_args = payload[1:]
    sender_name = _display_name(update)
    targets, err = _resolve_target_users(
        chat.id,
        sender.id,
        sender_name,
        user_args,
        context.bot_data.get("fixed_users"),
    )
    if err:
        await msg.reply_text(err)
        return

    mode_label = "padrao" if mode == "STANDARD" else "home office" if mode == "HOME" else "campo"
    lines: list[str] = []
    for target_id, target_name in targets:
        set_work_mode(chat.id, target_id, target_name, target_day, mode)
        lines.append(f"OK {target_name}: modo de {target_day:%d/%m/%Y} definido para {mode_label}.")

    await msg.reply_text("\n".join(lines))


def _today_window_utc(tz: ZoneInfo) -> tuple[datetime, datetime]:
    now_local = datetime.now(tz)
    return _day_window_utc(now_local.date(), tz)


def _day_window_utc(target_day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_local = datetime(target_day.year, target_day.month, target_day.day, 0, 0, 0, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _transition_state(state: int, action: str) -> int | None:
    # 0: inicio, 1: apos entrada, 2: apos almoco, 3: apos entrada_2, 4: apos saida
    if action == "IN":
        action = "ENTRY" if state == 0 else "RETURN" if state == 2 else "_INVALID"
    elif action == "OUT":
        action = "LUNCH_OUT" if state == 1 else "EXIT" if state == 3 else "_INVALID"

    if state == 0 and action == "ENTRY":
        return 1
    if state == 1 and action == "LUNCH_OUT":
        return 2
    if state == 0 and action == "RETURN":
        return 3
    if state == 2 and action == "RETURN":
        return 3
    if state == 3 and action == "EXIT":
        return 4
    return None


def _analyze_day_events(actions_in_order: list[str]) -> tuple[bool, bool, str]:
    state = 0
    invalid = False

    for action in actions_in_order:
        next_state = _transition_state(state, action)
        if next_state is None:
            invalid = True
            break
        state = next_state

    if invalid:
        return True, True, "sequencia invalida"
    if state == 0:
        return False, False, "sem registros"
    if state == 1:
        return True, False, "faltou /almoco"
    if state == 2:
        return False, False, "meio periodo fechado"
    if state == 3:
        return True, False, "faltou /saida"
    if "ENTRY" in actions_in_order or "IN" in actions_in_order:
        return False, False, "jornada completa"
    return False, False, "meio periodo (entrada_2 -> saida) fechado"


def _action_sort_rank(action: str) -> int:
    # Ordenacao para empates de timestamp na correcao manual.
    if action in {"ENTRY", "IN"}:
        return 0
    if action in {"LUNCH_OUT", "OUT"}:
        return 1
    if action == "RETURN":
        return 2
    if action == "EXIT":
        return 3
    return 9


def _parse_date_input(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_month_input(raw: str) -> tuple[int, int] | None:
    raw = raw.strip()
    if not raw:
        return None

    try:
        dt = datetime.strptime(raw, "%Y-%m")
        return dt.year, dt.month
    except ValueError:
        pass

    try:
        dt = datetime.strptime(raw, "%m/%Y")
        return dt.year, dt.month
    except ValueError:
        pass

    parsed_day = _parse_date_input(raw)
    if parsed_day is not None:
        return parsed_day.year, parsed_day.month
    return None


def _is_time_or_dash(token: str) -> bool:
    return token == "-" or _normalize_time_token(token) is not None


def _normalize_time_token(token: str) -> str | None:
    token = token.strip()
    if not TIME_TOKEN_RE.fullmatch(token):
        return None
    hour_str, minute_str = token.split(":", 1)
    hour = int(hour_str)
    minute = int(minute_str)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_correction_payload(args: list[str]) -> tuple[list[tuple[str, str]], list[str], str | None]:
    # Retorna (correcoes, user_args, erro). Correcoes: [(action, hh:mm), ...]
    # Modo 1: explicito -> <tipo> <HH:MM> [usuarios]
    # Modo 2: compacto -> <HH:MM|-> <HH:MM|-> <HH:MM|-> <HH:MM|-> [usuarios]
    if len(args) < 2:
        return [], [], "argumentos insuficientes"

    maybe_action = _normalize_text(args[0])
    if maybe_action in MANUAL_ACTIONS and len(args) >= 2:
        normalized_time = _normalize_time_token(args[1].strip())
        if normalized_time is None:
            return [], [], "hora invalida"
        return [(MANUAL_ACTIONS[maybe_action], normalized_time)], args[2:], None

    if len(args) < 4:
        return [], [], "argumentos insuficientes"
    block = [args[i].strip() for i in range(4)]
    if not all(_is_time_or_dash(token) for token in block):
        return [], [], "bloco de horarios invalido"

    corrections: list[tuple[str, str]] = []
    for idx, token in enumerate(block):
        if token == "-":
            continue
        normalized_time = _normalize_time_token(token)
        if normalized_time is None:
            return [], [], "hora invalida"
        corrections.append((CORRECTION_ORDER[idx][0], normalized_time))
    if not corrections:
        return [], [], "nenhum horario informado"
    return corrections, args[4:], None


def _apply_manual_correction(
    *,
    chat_id: int,
    user_id: int,
    user_name: str,
    action: str,
    target_day: date,
    time_raw: str,
    tz: ZoneInfo,
    now_local: datetime,
) -> tuple[bool, str]:
    try:
        t = datetime.strptime(time_raw, "%H:%M").time()
    except ValueError:
        return False, "Hora invalida. Use formato HH:MM, ex.: 13:30"

    manual_local = datetime(target_day.year, target_day.month, target_day.day, t.hour, t.minute, 0, tzinfo=tz)
    if manual_local > now_local:
        return False, "Nao e permitido corrigir horario futuro."

    start_utc, end_utc = _day_window_utc(target_day, tz)
    day_events = [p for p in list_punches_between(chat_id, start_utc, end_utc) if p.user_id == user_id]
    candidate_utc = manual_local.astimezone(timezone.utc)
    if any(p.ts_utc == candidate_utc and p.action == action for p in day_events):
        return False, "Ja existe esse mesmo registro nesse horario."

    timeline = [(p.ts_utc, p.action) for p in day_events]
    timeline.append((candidate_utc, action))
    timeline.sort(key=lambda item: (item[0], _action_sort_rank(item[1])))
    actions_after = [event_action for _, event_action in timeline]
    pending, invalid, reason = _analyze_day_events(actions_after)
    if invalid:
        return False, f"Correcao rejeitada: a sequencia do dia ficaria invalida ({reason})."

    add_punch(chat_id, user_id, user_name, action, candidate_utc)
    status = "ainda pendente" if pending else "sem pendencias"
    return True, (
        f"Correcao registrada: {ACTION_LABELS[action]} em {manual_local:%d/%m/%Y %H:%M}. Dia {status}."
    )


async def corrigir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    chat = update.effective_chat
    sender = update.effective_user
    msg = update.effective_message
    assert chat and sender and msg

    if len(context.args) < 2:
        await msg.reply_text(
            "Uso: /corrigir <entrada|almoco|entrada_2|saida> <HH:MM> [usuarios]\n"
            "ou: /corrigir <h1> <h2> <h3> <h4> [usuarios]\n"
            "ou: /corrigir <data> <entrada|almoco|entrada_2|saida> <HH:MM> [usuarios]\n"
            "ou: /corrigir <data> <h1> <h2> <h3> <h4> [usuarios]"
        )
        return

    tz: ZoneInfo = context.bot_data["tz"]
    now_local = datetime.now(tz)
    target_day = now_local.date()
    payload_args = context.args

    parsed_day = _parse_date_input(context.args[0].strip())
    if parsed_day is not None:
        target_day = parsed_day
        payload_args = context.args[1:]
        if len(payload_args) < 2:
            await msg.reply_text(
                "Uso: /corrigir <data> <entrada|almoco|entrada_2|saida> <HH:MM> [usuarios]\n"
                "ou: /corrigir <data> <h1> <h2> <h3> <h4> [usuarios]"
            )
            return

    corrections, user_args, parse_err = _parse_correction_payload(payload_args)
    if parse_err is not None:
        await msg.reply_text(
            "Parametros invalidos para /corrigir. "
            "Use tipo+hora ou bloco de 4 horarios (entrada almoco entrada_2 saida)."
        )
        return

    sender_name = _display_name(update)
    targets, err = _resolve_target_users(
        chat.id,
        sender.id,
        sender_name,
        user_args,
        context.bot_data.get("fixed_users"),
    )
    if err:
        await msg.reply_text(err)
        return

    lines: list[str] = []
    for target_id, target_name in targets:
        for action, time_raw in corrections:
            ok, result = _apply_manual_correction(
                chat_id=chat.id,
                user_id=target_id,
                user_name=target_name,
                action=action,
                target_day=target_day,
                time_raw=time_raw,
                tz=tz,
                now_local=now_local,
            )
            prefix = "OK" if ok else "ERRO"
            lines.append(f"{prefix} {target_name} [{ACTION_LABELS[action]} {time_raw}]: {result}")
    await msg.reply_text("\n".join(lines))


def _register_action(
    chat_id: int,
    user_id: int,
    user_name: str,
    action: str,
    allowed_previous: set[str | None],
    error_message: str,
    tz: ZoneInfo,
) -> tuple[bool, str]:
    last = get_last_punch(chat_id, user_id)
    previous = last.action if last else None
    if previous not in allowed_previous:
        return False, error_message

    now_utc = datetime.now(timezone.utc)
    add_punch(chat_id, user_id, user_name, action, now_utc)
    label = ACTION_LABELS.get(action, action)
    return True, f"{label} registrada: {now_utc.astimezone(tz):%d/%m/%Y %H:%M:%S}"


def _normalize_text(value: str) -> str:
    value = value.strip().lower()
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _resolve_target_users(
    chat_id: int,
    sender_id: int,
    sender_name: str,
    args: list[str],
    fixed_users: dict[str, tuple[int, str]] | None = None,
) -> tuple[list[tuple[int, str]], str | None]:
    if not args:
        return [(sender_id, sender_name)], None

    fixed_users = fixed_users or {}
    known_users = list_known_users(chat_id)
    by_id: dict[int, str] = {user.user_id: user.user_name for user in known_users}
    by_id[sender_id] = sender_name

    targets: list[tuple[int, str]] = []
    used_ids: set[int] = set()
    raw_tokens: list[str] = []
    for arg in args:
        raw_tokens.extend(part for part in arg.replace(",", " ").split() if part.strip())

    if not raw_tokens:
        return [(sender_id, sender_name)], None

    for token in raw_tokens:
        norm = _normalize_text(token)
        if norm in {"eu", "me", "mim"}:
            if sender_id not in used_ids:
                targets.append((sender_id, sender_name))
                used_ids.add(sender_id)
            continue

        if norm in fixed_users:
            fixed_id, fixed_name = fixed_users[norm]
            if fixed_id not in used_ids:
                targets.append((fixed_id, fixed_name))
                used_ids.add(fixed_id)
            continue

        candidates = [
            (user_id, user_name)
            for user_id, user_name in by_id.items()
            if norm and norm in _normalize_text(user_name)
        ]
        if not candidates:
            return [], f"Usuario nao encontrado para argumento: {token}"
        if len(candidates) > 1:
            sample = ", ".join(name for _, name in candidates[:4])
            return [], f"Argumento ambiguo '{token}'. Possiveis: {sample}"

        user_id, user_name = candidates[0]
        if user_id not in used_ids:
            targets.append((user_id, user_name))
            used_ids.add(user_id)

    if not targets:
        return [], "Nenhum usuario valido informado."
    return targets, None


async def _register_action_for_targets(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    allowed_previous: set[str | None],
    error_message: str,
) -> None:
    chat = update.effective_chat
    sender = update.effective_user
    msg = update.effective_message
    assert chat and sender and msg

    sender_name = _display_name(update)
    targets, err = _resolve_target_users(
        chat.id,
        sender.id,
        sender_name,
        context.args,
        context.bot_data.get("fixed_users"),
    )
    if err:
        await msg.reply_text(err)
        return

    tz: ZoneInfo = context.bot_data["tz"]
    lines: list[str] = []
    for target_id, target_name in targets:
        ok, result = _register_action(
            chat_id=chat.id,
            user_id=target_id,
            user_name=target_name,
            action=action,
            allowed_previous=allowed_previous,
            error_message=error_message,
            tz=tz,
        )
        prefix = "OK" if ok else "ERRO"
        lines.append(f"{prefix} {target_name}: {result}")

    await msg.reply_text("\n".join(lines))


def _previous_month(now_local: datetime) -> tuple[int, int]:
    year = now_local.year
    month = now_local.month
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _month_window_utc_from_year_month(
    tz: ZoneInfo,
    year: int,
    month: int,
) -> tuple[datetime, datetime]:
    start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=tz)
    if month == 12:
        end_local = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
    else:
        end_local = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _load_month_data(
    context: CallbackContext,
    year: int,
    month: int,
) -> tuple[int, int, list]:
    tz: ZoneInfo = context.bot_data["tz"]
    chat_id: int = context.bot_data["target_chat_id"]
    start_utc, end_utc = _month_window_utc_from_year_month(tz, year, month)
    punches = list(list_punches_between(chat_id, start_utc, end_utc))
    return year, month, punches


def _load_cumulative_data_until_month_end(
    context: CallbackContext,
    year: int,
    month: int,
) -> list:
    tz: ZoneInfo = context.bot_data["tz"]
    chat_id: int = context.bot_data["target_chat_id"]
    _start_utc, end_utc = _month_window_utc_from_year_month(tz, year, month)
    start_utc = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return list(list_punches_between(chat_id, start_utc, end_utc))


def _month_start_end_dates(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def _load_daily_target_overrides_between_by_user(
    chat_id: int,
    start_day: date,
    end_day: date,
    targets: list[tuple[int, str]] | None = None,
) -> dict[int, dict[date, timedelta]]:
    target_ids = {user_id for user_id, _name in targets} if targets else None
    rows = list_daily_targets_between(chat_id, start_day, end_day, user_ids=target_ids)

    by_user: dict[int, dict[date, timedelta]] = defaultdict(dict)
    for row in rows:
        by_user[row.user_id][row.target_day] = row.target
    return by_user


def _format_duration_hhmm(total: timedelta) -> str:
    total_minutes = int(total.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


def _format_signed_duration_hhmm(diff: timedelta) -> str:
    total_minutes = int(abs(diff.total_seconds()) // 60)
    hours, minutes = divmod(total_minutes, 60)
    if diff > timedelta():
        return f"+ {hours:02d}:{minutes:02d}"
    if diff < timedelta():
        return f"-{hours:02d}:{minutes:02d}"
    return "00:00"


def _parse_report_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[int, int, list[tuple[int, str]] | None, str | None]:
    tz: ZoneInfo = context.bot_data["tz"]
    now_local = datetime.now(tz)
    year, month = now_local.year, now_local.month
    user_args = context.args

    if context.args:
        parsed_month = _parse_month_input(context.args[0])
        if parsed_month is not None:
            year, month = parsed_month
            user_args = context.args[1:]

    if not user_args:
        return year, month, None, None

    chat = update.effective_chat
    sender = update.effective_user
    assert chat and sender

    sender_name = _display_name(update)
    targets, err = _resolve_target_users(
        chat.id,
        sender.id,
        sender_name,
        user_args,
        context.bot_data.get("fixed_users"),
    )
    if err:
        return year, month, None, err
    return year, month, targets, None


async def _generate_and_send_month_report(
    context: CallbackContext,
    year: int,
    month: int,
    targets: list[tuple[int, str]] | None = None,
    manual: bool = False,
) -> None:
    tz: ZoneInfo = context.bot_data["tz"]
    chat_id: int = context.bot_data["target_chat_id"]
    year, month, punches = _load_month_data(context, year=year, month=month)
    cumulative_punches = _load_cumulative_data_until_month_end(context, year=year, month=month)
    overrides_by_user = _load_daily_target_overrides_by_user(chat_id, year, month, targets=targets)
    work_modes_by_user = _load_work_modes_by_user(chat_id, year, month, targets=targets)
    if targets is not None:
        target_ids = {user_id for user_id, _ in targets}
        punches = [p for p in punches if p.user_id in target_ids]
        cumulative_punches = [p for p in cumulative_punches if p.user_id in target_ids]
    report_file = build_month_report(
        punches,
        str(tz),
        year,
        month,
        cumulative_punches=cumulative_punches,
        daily_target_overrides_by_user=overrides_by_user,
        work_modes_by_user=work_modes_by_user,
    )

    caption = f"Planilha de ponto {month:02d}/{year}"
    if targets is not None:
        target_names = ", ".join(user_name for _, user_name in targets)
        caption += f" (filtrado: {target_names})"
    if manual:
        caption += " (gerada manualmente)"

    with report_file.open("rb") as f:
        await context.bot.send_document(chat_id=chat_id, document=f, filename=report_file.name, caption=caption)


async def _generate_and_send_month_report_images(
    context: CallbackContext,
    year: int,
    month: int,
    targets: list[tuple[int, str]] | None = None,
    manual: bool = False,
) -> None:
    tz: ZoneInfo = context.bot_data["tz"]
    chat_id: int = context.bot_data["target_chat_id"]
    year, month, punches = _load_month_data(context, year=year, month=month)
    cumulative_punches = _load_cumulative_data_until_month_end(context, year=year, month=month)
    overrides_by_user = _load_daily_target_overrides_by_user(chat_id, year, month, targets=targets)
    work_modes_by_user = _load_work_modes_by_user(chat_id, year, month, targets=targets)
    if targets is not None:
        target_ids = {user_id for user_id, _ in targets}
        punches = [p for p in punches if p.user_id in target_ids]
        cumulative_punches = [p for p in cumulative_punches if p.user_id in target_ids]
    image_files = build_month_report_images(
        punches,
        str(tz),
        year,
        month,
        cumulative_punches=cumulative_punches,
        daily_target_overrides_by_user=overrides_by_user,
        work_modes_by_user=work_modes_by_user,
    )

    for user_name, image_file in image_files:
        caption = f"Tabela de ponto {month:02d}/{year} - {user_name}"
        if manual:
            caption += " (gerada manualmente)"
        with image_file.open("rb") as f:
            await context.bot.send_photo(chat_id=chat_id, photo=f, caption=caption)


async def mes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    assert update.effective_message
    year, month, targets, err = _parse_report_request(update, context)
    if err:
        await update.effective_message.reply_text(err)
        return
    await _generate_and_send_month_report(context, year=year, month=month, targets=targets, manual=True)


async def mes_png(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    assert update.effective_message
    year, month, targets, err = _parse_report_request(update, context)
    if err:
        await update.effective_message.reply_text(err)
        return
    await _generate_and_send_month_report_images(context, year=year, month=month, targets=targets, manual=True)


async def resumo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_allowed_chat_or_reply(update, context.bot_data["target_chat_id"]):
        return

    assert update.effective_message and update.effective_user
    tz: ZoneInfo = context.bot_data["tz"]
    chat_id: int = context.bot_data["target_chat_id"]
    year, month, targets, err = _parse_report_request(update, context)
    if err:
        await update.effective_message.reply_text(err)
        return

    if targets is None:
        sender = update.effective_user
        sender_name = _display_name(update)
        targets = [(sender.id, sender_name)]

    _year, _month, month_punches = _load_month_data(context, year=year, month=month)
    cumulative_punches = _load_cumulative_data_until_month_end(context, year=year, month=month)
    target_ids = {user_id for user_id, _name in targets}
    month_punches = [p for p in month_punches if p.user_id in target_ids]
    cumulative_punches = [p for p in cumulative_punches if p.user_id in target_ids]

    month_overrides = _load_daily_target_overrides_by_user(chat_id, year, month, targets=targets)
    _month_start, month_end = _month_start_end_dates(year, month)
    cumulative_overrides = _load_daily_target_overrides_between_by_user(
        chat_id,
        start_day=date(1970, 1, 1),
        end_day=month_end,
        targets=targets,
    )

    summaries = build_month_hours_summary(
        month_punches,
        str(tz),
        year,
        month,
        cumulative_punches=cumulative_punches,
        daily_target_overrides_by_user=month_overrides,
        cumulative_daily_target_overrides_by_user=cumulative_overrides,
        user_scope=targets,
    )

    lines = [f"Resumo de horas {month:02d}/{year}:"]
    for summary in summaries:
        lines.append(
            f"- {summary.user_name}: "
            f"cumprido no mes {_format_duration_hhmm(summary.month_total)} | "
            f"saldo mensal {_format_signed_duration_hhmm(summary.month_balance)} | "
            f"saldo acumulado {_format_signed_duration_hhmm(summary.cumulative_balance)}"
        )

    if not summaries:
        lines.append("- Sem dados para os usuarios informados.")

    await update.effective_message.reply_text("\n".join(lines))


async def scheduled_monthly_report(context: CallbackContext) -> None:
    tz: ZoneInfo = context.bot_data["tz"]
    year, month = _previous_month(datetime.now(tz))
    await _generate_and_send_month_report(context, year=year, month=month, manual=False)


async def scheduled_pending_alert(context: CallbackContext) -> None:
    tz: ZoneInfo = context.bot_data["tz"]
    chat_id: int = context.bot_data["target_chat_id"]
    start_utc, end_utc = _today_window_utc(tz)
    punches = list(list_punches_between(chat_id, start_utc, end_utc))
    by_user: dict[tuple[int, str], list[str]] = defaultdict(list)

    for punch in punches:
        by_user[(punch.user_id, punch.user_name)].append(punch.action)

    pending_users: list[str] = []
    for (_user_id, user_name), actions in sorted(by_user.items(), key=lambda item: item[0][1].lower()):
        pending, _invalid, reason = _analyze_day_events(actions)
        if pending:
            pending_users.append(f"- {user_name}: {reason}")

    if not pending_users:
        return

    text = "Aviso de pendencias de ponto (20:00):\n" + "\n".join(pending_users)
    await context.bot.send_message(chat_id=chat_id, text=text)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Erro nao tratado no bot", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Ocorreu um erro interno ao processar seu comando. Tente novamente em instantes."
        )


def main() -> None:
    load_dotenv()

    token = _require_env("BOT_TOKEN")
    target_chat_id = int(_require_env("TARGET_CHAT_ID"))
    tz_name = os.getenv("TIMEZONE", "America/Sao_Paulo")
    tz = ZoneInfo(tz_name)

    init_db()

    app = Application.builder().token(token).build()
    app.bot_data["target_chat_id"] = target_chat_id
    app.bot_data["tz"] = tz
    app.bot_data["fixed_users"] = _load_fixed_users_from_env()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("entrada", entrada))
    app.add_handler(CommandHandler("almoco", almoco))
    app.add_handler(CommandHandler("entrada_2", entrada_2))
    app.add_handler(CommandHandler("saida", saida))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("time_base", time_base))
    app.add_handler(CommandHandler("workmode", workmode))
    app.add_handler(CommandHandler("corrigir", corrigir))
    app.add_handler(CommandHandler("mes", mes))
    app.add_handler(CommandHandler("mes_png", mes_png))
    app.add_handler(CommandHandler("resumo", resumo))
    app.add_handler(CommandHandler("chat_id", chat_id))
    app.add_error_handler(on_error)

    app.job_queue.run_monthly(
        scheduled_monthly_report,
        when=datetime.strptime("08:00", "%H:%M").time().replace(tzinfo=tz),
        day=1,
        name="monthly_report",
    )
    app.job_queue.run_daily(
        scheduled_pending_alert,
        time=datetime.strptime("20:00", "%H:%M").time().replace(tzinfo=tz),
        name="pending_alert_20h",
    )

    logger.info("Bot iniciado para chat_id=%s timezone=%s", target_chat_id, tz_name)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
