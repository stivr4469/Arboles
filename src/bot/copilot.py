import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import text

from src.core.database import AsyncSessionLocal
from src.core.encryption import encrypt

logger = logging.getLogger(__name__)
router = Router()

# MVP: один тенант — в Phase 2 заменить на lookup по telegram user_id
_ONBOARDING_TENANT_ID = "00000000-0000-0000-0000-000000000001"


class Onboarding(StatesGroup):
    waiting_for_fb_token = State()
    waiting_for_keitaro_url = State()
    waiting_for_keitaro_key = State()


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "<b>Привет! Я Ad-Pilot.</b>\n\n"
        "Каждое утро в 08:30 присылаю отчёт по твоим объявлениям.\n"
        "Если что-то идёт не так — сразу пишу.\n\n"
        "/connect — подключить Facebook и Keitaro\n"
        "/report — отчёт прямо сейчас\n"
        "/help — справка",
    )


@router.message(Command("connect"))
async def cmd_connect(message: Message, state: FSMContext) -> None:
    await message.answer(
        "Шаг 1 из 3.\n\n"
        "Отправь <b>Meta System User Token</b>.\n"
        "<i>Сообщение удалится автоматически, токен сохранится зашифрованным.</i>"
    )
    await state.set_state(Onboarding.waiting_for_fb_token)


@router.message(Onboarding.waiting_for_fb_token)
async def process_fb_token(message: Message, state: FSMContext) -> None:
    token = message.text or ""
    if not token.strip():
        await message.answer("❌ Токен не может быть пустым. Попробуйте снова.")
        return

    await state.update_data(fb_token=token)
    try:
        await message.delete()
    except Exception:
        pass  # нет прав на удаление — не критично

    await message.answer(
        "✅ Токен принят.\n\n"
        "Шаг 2 из 3.\n"
        "Отправь адрес Keitaro, например: <code>https://tracker.myteam.com</code>"
    )
    await state.set_state(Onboarding.waiting_for_keitaro_url)


@router.message(Onboarding.waiting_for_keitaro_url)
async def process_keitaro_url(message: Message, state: FSMContext) -> None:
    url = (message.text or "").strip().rstrip("/")
    if not url.startswith("http"):
        await message.answer("❌ URL должен начинаться с http:// или https://. Попробуйте снова.")
        return

    await state.update_data(keitaro_url=url)
    await message.answer(
        "✅ Адрес принят.\n\n"
        "Шаг 3 из 3.\n"
        "Отправь <b>API-ключ Keitaro</b> (Admin key).\n"
        "<i>Сообщение удалится автоматически.</i>"
    )
    await state.set_state(Onboarding.waiting_for_keitaro_key)


@router.message(Onboarding.waiting_for_keitaro_key)
async def process_keitaro_key(message: Message, state: FSMContext) -> None:
    keitaro_key = message.text or ""
    if not keitaro_key.strip():
        await message.answer("❌ API-ключ не может быть пустым. Попробуйте снова.")
        return

    user_data = await state.get_data()
    fb_token: str = user_data["fb_token"]
    keitaro_url: str = user_data["keitaro_url"]

    try:
        await message.delete()
    except Exception:
        pass

    try:
        fb_cipher, fb_iv, fb_tag = encrypt(fb_token)
        k_cipher, k_iv, k_tag = encrypt(keitaro_key)

        async with AsyncSessionLocal() as session:
            async with session.begin():
                await session.execute(
                    text("SET LOCAL app.current_tenant_id = :tid"),
                    {"tid": _ONBOARDING_TENANT_ID},
                )
                await session.execute(
                    text(
                        "INSERT INTO ad_accounts "
                        "(tenant_id, platform, account_id, encrypted_token, token_iv, token_tag) "
                        "VALUES (:tid, 'facebook', :account_id, :token, :iv, :tag) "
                        "ON CONFLICT (tenant_id, platform, account_id) DO UPDATE "
                        "SET encrypted_token=EXCLUDED.encrypted_token, "
                        "    token_iv=EXCLUDED.token_iv, token_tag=EXCLUDED.token_tag"
                    ),
                    {
                        "tid": _ONBOARDING_TENANT_ID,
                        "account_id": str(message.from_user.id),
                        "token": fb_cipher, "iv": fb_iv, "tag": fb_tag,
                    },
                )
                await session.execute(
                    text(
                        "INSERT INTO keitaro_configs "
                        "(tenant_id, base_url, encrypted_api_key, api_key_iv, api_key_tag, "
                        " ad_id_param, is_active) "
                        "VALUES (:tid, :base_url, :api_key, :iv, :tag, 'sub3', true) "
                        "ON CONFLICT (tenant_id) DO UPDATE "
                        "SET base_url=EXCLUDED.base_url, "
                        "    encrypted_api_key=EXCLUDED.encrypted_api_key, "
                        "    api_key_iv=EXCLUDED.api_key_iv, api_key_tag=EXCLUDED.api_key_tag, "
                        "    is_active=true"
                    ),
                    {
                        "tid": _ONBOARDING_TENANT_ID,
                        "base_url": keitaro_url,
                        "api_key": k_cipher, "iv": k_iv, "tag": k_tag,
                    },
                )

        await state.clear()
        await message.answer(
            "🎉 Готово! Всё подключено.\n\n"
            "Данные начнут собираться в течение часа.\n"
            "Первый отчёт — завтра в 08:30."
        )
        logger.info("Onboarding complete for telegram user_id=%s", message.from_user.id)

    except Exception:
        logger.exception("Onboarding DB write failed for user_id=%s", message.from_user.id)
        await state.clear()
        await message.answer(
            "❌ Что-то пошло не так. Попробуй /connect ещё раз."
        )


@router.message(Command("report"))
async def cmd_report(message: Message) -> None:
    await message.answer("⏳ Считаю...")
    try:
        from src.collector.tasks import send_morning_report_task
        send_morning_report_task.delay()
        await message.answer("✅ Отчёт придёт через несколько секунд.")
    except Exception as e:
        logger.exception("Failed to trigger report: %s", e)
        await message.answer("❌ Не удалось запустить. Проверь логи.")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "/connect — подключить Facebook и Keitaro\n"
        "/report — получить отчёт прямо сейчас\n"
        "/start — начало работы\n\n"
        "<i>Отчёт каждый день в 08:30. "
        "Если что-то сломалось — напишу сразу.</i>",
    )
