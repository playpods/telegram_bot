import os
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

# ========== НАСТРОЙКА ==========
# ТОКЕН БОТА (получить у @BotFather)
BOT_TOKEN = "8435489975:AAF-Sn80y-MLTtsuIXkfF7Kulh2rAfMzPeo"  # ← ЗАМЕНИ НА СВОЙ ТОКЕН!

# НАСТРОЙКИ ПРОКСИ (ЕСЛИ НУЖЕН)
USE_PROXY = True  # True - использовать прокси, False - без прокси
PROXY_URL = "http://193.239.86.180:80"  # URL твоего HTTPS прокси
# Примеры: http://127.0.0.1:8080, https://proxy.example.com:8080
# Если нужен логин/пароль: http://user:pass@127.0.0.1:8080

# НАСТРОЙКИ БОТА
TAG_INTERVAL = 2  # Интервал между тегами в секундах
MAX_PLAYERS = 10000  # Максимальное количество игроков
CONNECTION_TIMEOUT = 60.0  # Таймаут подключения в секундах

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== СОСТОЯНИЕ ТЕГА ==========
@dataclass
class TagState:
    """Состояние тега в чате"""
    players: List[dict] = field(default_factory=list)
    current_index: int = 0
    is_active: bool = False
    text: str = ""
    start_time: Optional[datetime] = None
    total_players: int = 0
    tagged_count: int = 0
    paused: bool = False
    
    def reset(self):
        """Сбросить состояние"""
        self.players = []
        self.current_index = 0
        self.is_active = False
        self.text = ""
        self.start_time = None
        self.total_players = 0
        self.tagged_count = 0
        self.paused = False

# Хранилище состояний для всех чатов
chat_states: Dict[int, TagState] = defaultdict(TagState)
active_tasks: Dict[int, asyncio.Task] = {}

# ========== ОСНОВНОЙ КЛАСС БОТА ==========
class TagBot:
    """Основной класс бота"""
    
    def __init__(self, token: str):
        self.token = token
        self.application = None
        
    def setup_handlers(self):
        """Настройка обработчиков команд"""
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("tagall", self.tag_all_command))
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(CommandHandler("reset", self.reset_command))
        self.application.add_handler(CommandHandler("next", self.next_command))
        self.application.add_handler(CommandHandler("pause", self.pause_command))
        self.application.add_handler(CommandHandler("resume", self.resume_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        
        # Обработчик кнопок
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        
        # Обработчик ошибок
        self.application.add_error_handler(self.error_handler)
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start - приветствие и справка"""
        user = update.effective_user
        welcome_text = f"""
👋 Привет, {user.first_name}! 

Я - **TagMaster Bot** - профессиональный бот для массового тега участников в чатах!

📋 **Основные команды:**
• `/tagall [текст]` - Начать поочередный тег всех участников
• `/stop` - Остановить текущий тег
• `/pause` - Приостановить тег
• `/resume` - Возобновить тег
• `/next` - Перейти к следующему участнику
• `/reset` - Сбросить всё и начать сначала
• `/status` - Показать статус текущего тега
• `/help` - Показать это сообщение

⚙️ **Как это работает:**
1️⃣ Бот собирает список участников чата
2️⃣ Начинает по очереди тегать каждого
3️⃣ Между тегами пауза 2 секунды
4️⃣ Можно добавлять свой текст к упоминанию
5️⃣ Администраторы могут управлять процессом

🔒 **Требования:**
• Бот должен быть администратором чата
• Только администраторы могут управлять тегом

💡 **Пример использования:**
`/tagall Внимание! Важное объявление для всех!`

📊 **Статистика:** Бот отслеживает прогресс и показывает статус

🚀 **Готов к работе! Используй /tagall для начала.**
"""
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /help - подробная справка"""
        help_text = f"""
📚 **Подробная справка по командам:**

**Основные команды:**
• `/tagall [текст]` - Запускает процесс тега
• `/stop` - Немедленно останавливает текущий тег
• `/pause` - Приостанавливает тег
• `/resume` - Продолжает приостановленный тег
• `/next` - Принудительно переключает на следующего
• `/reset` - Полностью сбрасывает текущий тег
• `/status` - Показывает прогресс тега

**Дополнительные возможности:**
• Автоматическая пауза в {TAG_INTERVAL} секунд между тегами
• Отображение прогресса (номер/всего)
• Кнопки управления под каждым сообщением
• Защита от спама и ошибок
• Логирование всех действий

**Для администраторов:**
• Все команды доступны только администраторам чата
• Бот должен быть администратором для получения списка участников

**Ограничения:**
• Максимум {MAX_PLAYERS} участников за один тег
• Бот автоматически исключает себя из списка

❓ **Проблемы?**
Убедитесь, что:
1. Бот является администратором чата
2. У бота есть права на чтение сообщений
3. Вы используете команды в правильном чате
"""
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def is_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Проверка прав администратора"""
        try:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            
            member = await context.bot.get_chat_member(chat_id, user_id)
            return member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
        except Exception as e:
            logger.error(f"Ошибка проверки прав: {e}")
            return False

    async def get_chat_members_safe(self, bot, chat_id: int) -> List[dict]:
        """Безопасное получение участников чата"""
        members = []
        try:
            admins = await bot.get_chat_administrators(chat_id)
            for admin in admins:
                members.append({
                    'id': admin.user.id,
                    'username': admin.user.username,
                    'first_name': admin.user.first_name,
                    'last_name': admin.user.last_name,
                    'full_name': admin.user.full_name or admin.user.first_name,
                    'is_bot': admin.user.is_bot
                })
        except Exception as e:
            logger.error(f"Ошибка получения участников: {e}")
            raise
        
        return members

    async def tag_all_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /tagall - начать тег всех участников"""
        chat = update.effective_chat
        
        if not await self.is_admin(update, context):
            await update.message.reply_text("❌ Эта команда доступна только администраторам чата!")
            return
        
        state = chat_states[chat.id]
        if state.is_active and not state.paused:
            await update.message.reply_text(
                "⚠️ Уже идет активный тег!\n"
                "Используй /pause для паузы или /stop для остановки."
            )
            return
        
        status_msg = await update.message.reply_text("🔄 Получаю список участников чата...")
        
        try:
            members = await self.get_chat_members_safe(context.bot, chat.id)
            
            unique_members = {}
            for member in members:
                if member['id'] != context.bot.id and not member['is_bot']:
                    unique_members[member['id']] = member
            
            players = list(unique_members.values())
            
            if len(players) < 2:
                await status_msg.edit_text("❌ В чате недостаточно участников (нужно минимум 2 человека).")
                return
            
            if len(players) > MAX_PLAYERS:
                await status_msg.edit_text(f"❌ Слишком много участников! Максимум: {MAX_PLAYERS}")
                return
            
            state.reset()
            state.players = players
            state.total_players = len(players)
            state.is_active = True
            state.start_time = datetime.now()
            state.text = " ".join(context.args) if context.args else ""
            
            await status_msg.edit_text(
                f"✅ Тег запущен!\n"
                f"👥 Всего участников: {len(players)}\n"
                f"⏱ Интервал: {TAG_INTERVAL} сек.\n"
                f"{'📝 Текст: ' + state.text if state.text else ''}\n\n"
                f"Начинаем через 2 секунды..."
            )
            
            await asyncio.sleep(2)
            
            if chat.id in active_tasks and not active_tasks[chat.id].done():
                active_tasks[chat.id].cancel()
            
            task = asyncio.create_task(self.process_tag(chat.id, context))
            active_tasks[chat.id] = task
            
        except Exception as e:
            logger.error(f"Ошибка при запуске тега: {e}")
            await status_msg.edit_text(f"❌ Ошибка: {str(e)}\nУбедись, что бот является администратором чата.")

    async def process_tag(self, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Процесс поочередного тега"""
        state = chat_states[chat_id]
        
        while state.is_active and state.current_index < len(state.players):
            try:
                if state.paused:
                    await asyncio.sleep(1)
                    continue
                
                player = state.players[state.current_index]
                
                mention = f"@{player['username']}" if player['username'] else f"[{player['full_name']}](tg://user?id={player['id']})"
                
                message = f"🔔 **Уведомление!**\n\n"
                message += f"👤 {mention}\n"
                
                if state.text:
                    message += f"📝 {state.text}\n\n"
                
                message += f"📊 Прогресс: {state.current_index + 1}/{state.total_players}"
                
                keyboard = [
                    [InlineKeyboardButton("➡️ Следующий", callback_data=f"next_{chat_id}")],
                    [InlineKeyboardButton("⏸ Пауза", callback_data=f"pause_{chat_id}"),
                     InlineKeyboardButton("⏹ Стоп", callback_data=f"stop_{chat_id}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
                
                state.tagged_count += 1
                state.current_index += 1
                
                if state.current_index < len(state.players):
                    await asyncio.sleep(TAG_INTERVAL)
                
            except Exception as e:
                logger.error(f"Ошибка при теге: {e}")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка при теге: {str(e)}"
                )
                break
        
        if state.is_active:
            if state.current_index >= len(state.players):
                duration = (datetime.now() - state.start_time).total_seconds() if state.start_time else 0
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ **Тег успешно завершен!**\n\n"
                         f"👥 Всего участников: {state.total_players}\n"
                         f"⏱ Время выполнения: {duration:.1f} сек.\n"
                         f"📊 Среднее время на участника: {duration/state.total_players:.1f} сек.\n\n"
                         f"Используй /tagall для нового тега."
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏹ Тег остановлен.\n"
                         f"✅ Протегано: {state.tagged_count}/{state.total_players}"
                )
            
            state.is_active = False
            state.paused = False
            
        if chat_id in active_tasks:
            del active_tasks[chat_id]

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /stop - остановить тег"""
        chat = update.effective_chat
        
        if not await self.is_admin(update, context):
            await update.message.reply_text("❌ Только администраторы могут использовать эту команду!")
            return
        
        state = chat_states[chat.id]
        if state.is_active:
            state.is_active = False
            state.paused = False
            
            if chat.id in active_tasks and not active_tasks[chat.id].done():
                active_tasks[chat.id].cancel()
                del active_tasks[chat.id]
            
            await update.message.reply_text(
                f"⏹ Тег остановлен!\n"
                f"✅ Протегано: {state.tagged_count}/{state.total_players}"
            )
        else:
            await update.message.reply_text("❌ Нет активного тега для остановки.")

    async def pause_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /pause - приостановить тег"""
        chat = update.effective_chat
        
        if not await self.is_admin(update, context):
            await update.message.reply_text("❌ Только администраторы могут использовать эту команду!")
            return
        
        state = chat_states[chat.id]
        if state.is_active and not state.paused:
            state.paused = True
            await update.message.reply_text(
                f"⏸ Тег приостановлен!\n"
                f"📊 Прогресс: {state.tagged_count}/{state.total_players}\n"
                f"Используй /resume для продолжения."
            )
        else:
            await update.message.reply_text("❌ Нет активного тега для паузы.")

    async def resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /resume - продолжить тег"""
        chat = update.effective_chat
        
        if not await self.is_admin(update, context):
            await update.message.reply_text("❌ Только администраторы могут использовать эту команду!")
            return
        
        state = chat_states[chat.id]
        if state.is_active and state.paused:
            state.paused = False
            await update.message.reply_text(
                f"▶️ Тег продолжен!\n"
                f"📊 Прогресс: {state.tagged_count}/{state.total_players}"
            )
        else:
            await update.message.reply_text("❌ Нет приостановленного тега.")

    async def next_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /next - принудительно перейти к следующему"""
        chat = update.effective_chat
        
        if not await self.is_admin(update, context):
            await update.message.reply_text("❌ Только администраторы могут использовать эту команду!")
            return
        
        state = chat_states[chat.id]
        if state.is_active and not state.paused:
            if state.current_index < len(state.players):
                state.current_index += 1
                await update.message.reply_text(f"⏩ Переход к следующему игроку...")
            else:
                await update.message.reply_text("❌ Все игроки уже протеганы!")
        else:
            await update.message.reply_text("❌ Нет активного тега для перехода.")

    async def reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /reset - полный сброс"""
        chat = update.effective_chat
        
        if not await self.is_admin(update, context):
            await update.message.reply_text("❌ Только администраторы могут использовать эту команду!")
            return
        
        state = chat_states[chat.id]
        state.reset()
        
        if chat.id in active_tasks and not active_tasks[chat.id].done():
            active_tasks[chat.id].cancel()
            del active_tasks[chat.id]
        
        await update.message.reply_text("🔄 Тег полностью сброшен! Используй /tagall для нового тега.")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /status - показать статус тега"""
        chat = update.effective_chat
        state = chat_states[chat.id]
        
        if not state.is_active:
            await update.message.reply_text("📊 В данный момент нет активного тега.")
            return
        
        status_text = f"""
📊 **Статус тега:**

👥 Всего участников: {state.total_players}
✅ Протегано: {state.tagged_count}
📈 Осталось: {state.total_players - state.tagged_count}
⏳ Прогресс: {int((state.tagged_count / state.total_players) * 100) if state.total_players > 0 else 0}%

⏱ Статус: {'▶️ Активен' if not state.paused else '⏸ На паузе'}
🕐 Начало: {state.start_time.strftime('%H:%M:%S') if state.start_time else 'Не указано'}

📝 Текст: {state.text if state.text else 'Не указан'}
"""
        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        chat_id = query.message.chat_id
        user_id = query.from_user.id
        
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            is_admin = member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
        except:
            await query.edit_message_text("❌ Ошибка проверки прав!")
            return
        
        if not is_admin:
            await query.edit_message_text("❌ Только администраторы могут управлять тегом!")
            return
        
        state = chat_states[chat_id]
        
        if data.startswith("next_"):
            if state.is_active and not state.paused:
                if state.current_index < len(state.players):
                    await query.delete_message()
                    await query.message.reply_text(f"⏩ Переход к следующему игроку...")
                else:
                    await query.edit_message_text("❌ Все игроки уже протеганы!")
            else:
                await query.edit_message_text("❌ Нет активного тега для перехода.")
                
        elif data.startswith("pause_"):
            if state.is_active and not state.paused:
                state.paused = True
                await query.edit_message_text(
                    f"⏸ Тег приостановлен!\n"
                    f"📊 Прогресс: {state.tagged_count}/{state.total_players}"
                )
            else:
                await query.edit_message_text("❌ Нет активного тега для паузы.")
                
        elif data.startswith("stop_"):
            if state.is_active:
                state.is_active = False
                state.paused = False
                
                if chat_id in active_tasks and not active_tasks[chat_id].done():
                    active_tasks[chat_id].cancel()
                    del active_tasks[chat_id]
                
                await query.edit_message_text(
                    f"⏹ Тег остановлен!\n"
                    f"✅ Протегано: {state.tagged_count}/{state.total_players}"
                )
            else:
                await query.edit_message_text("❌ Нет активного тега для остановки.")

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок"""
        logger.error(f"Ошибка: {context.error}")
        
        if update and update.effective_chat:
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ Произошла ошибка: {str(context.error)[:200]}"
                )
            except:
                pass

    async def post_init(self, application: Application):
        """Действия после инициализации"""
        logger.info("🚀 Бот TagMaster успешно запущен!")
        logger.info(f"📊 Интервал между тегами: {TAG_INTERVAL} сек.")
        logger.info(f"👥 Максимум участников: {MAX_PLAYERS}")
        if USE_PROXY:
            logger.info(f"🔒 Используется прокси: {PROXY_URL}")

    def run(self):
        """Запуск бота"""
        # Настройка прокси
        if USE_PROXY:
            logger.info(f"🔒 Подключаюсь через прокси: {PROXY_URL}")
            request = HTTPXRequest(
                connect_timeout=CONNECTION_TIMEOUT,
                read_timeout=CONNECTION_TIMEOUT,
                write_timeout=CONNECTION_TIMEOUT,
                proxy_url=PROXY_URL
            )
        else:
            logger.info("🌐 Прямое подключение без прокси")
            request = HTTPXRequest(
                connect_timeout=CONNECTION_TIMEOUT,
                read_timeout=CONNECTION_TIMEOUT,
                write_timeout=CONNECTION_TIMEOUT
            )
        
        # Создаем приложение с нашим request
        self.application = (
            Application.builder()
            .token(self.token)
            .request(request)
            .build()
        )

        # Настраиваем обработчики
        self.setup_handlers()

        # Добавляем post_init
        self.application.post_init = self.post_init

        # Запускаем бота
        logger.info("Бот запускается...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

# ========== ЗАПУСК ==========
def main():
    """Главная функция"""
    bot = TagBot(BOT_TOKEN)
    bot.run()

if __name__ == "__main__":
    main()