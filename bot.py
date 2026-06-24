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

# ========== НАСТРОЙКА ==========
BOT_TOKEN = "8435489975:AAF-Sn80y-MLTtsuIXkfF7Kulh2rAfMzPeo"

TAG_INTERVAL = 2
MAX_PLAYERS = 2000

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== СОСТОЯНИЕ ТЕГА ==========
@dataclass
class TagState:
    players: List[dict] = field(default_factory=list)
    current_index: int = 0
    is_active: bool = False
    text: str = ""
    start_time: Optional[datetime] = None
    total_players: int = 0
    tagged_count: int = 0
    paused: bool = False
    
    def reset(self):
        self.players = []
        self.current_index = 0
        self.is_active = False
        self.text = ""
        self.start_time = None
        self.total_players = 0
        self.tagged_count = 0
        self.paused = False

chat_states: Dict[int, TagState] = defaultdict(TagState)
active_tasks: Dict[int, asyncio.Task] = {}

# ========== ОСНОВНОЙ КЛАСС БОТА ==========
class TagBot:
    
    def __init__(self, token: str):
        self.token = token
        self.application = None
        
    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("tagall", self.tag_all_command))
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(CommandHandler("reset", self.reset_command))
        self.application.add_handler(CommandHandler("next", self.next_command))
        self.application.add_handler(CommandHandler("pause", self.pause_command))
        self.application.add_handler(CommandHandler("resume", self.resume_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CallbackQueryHandler(self.button_callback))
        self.application.add_error_handler(self.error_handler)
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        welcome_text = f"""
👋 Привет, {user.first_name}! 

Я - **TagMaster Bot** - бот для массового тега участников!

📋 **Основные команды:**
• `/tagall` - Начать тег всех участников
• `/stop` - Остановить текущий тег
• `/pause` - Приостановить тег
• `/resume` - Возобновить тег
• `/status` - Показать статус

🔒 **Требования:**
• Бот должен быть администратором чата
• Только администраторы могут управлять тегом

🚀 **Используй /tagall для начала!**
"""
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        help_text = """
📚 **Команды:**

• `/tagall` - Запускает тег всех участников
• `/stop` - Останавливает текущий тег
• `/pause` - Приостанавливает тег
• `/resume` - Продолжает тег
• `/status` - Показывает прогресс

**Важно:**
• Бот должен быть администратором
• Команды доступны только админам
• Интервал между тегами: 2 секунды
"""
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

    async def is_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        try:
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            member = await context.bot.get_chat_member(chat_id, user_id)
            return member.status in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]
        except Exception as e:
            logger.error(f"Ошибка проверки прав: {e}")
            return False

    async def get_all_chat_members(self, bot, chat_id: int) -> List[dict]:
        """Получение ВСЕХ участников чата (не только администраторов)"""
        members = []
        try:
            # Получаем всех участников чата
            async for member in bot.get_chat_members(chat_id):
                # Пропускаем ботов
                if member.user.is_bot:
                    continue
                    
                members.append({
                    'id': member.user.id,
                    'username': member.user.username,
                    'first_name': member.user.first_name,
                    'last_name': member.user.last_name,
                    'full_name': member.user.full_name or member.user.first_name,
                    'is_bot': member.user.is_bot
                })
                
                # Ограничение на количество
                if len(members) >= MAX_PLAYERS:
                    break
                    
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
        
        status_msg = await update.message.reply_text("🔄 Получаю список всех участников чата...")
        
        try:
            # Получаем ВСЕХ участников
            members = await self.get_all_chat_members(context.bot, chat.id)
            
            # Убираем дубликаты и бота
            unique_members = {}
            for member in members:
                if member['id'] != context.bot.id and not member['is_bot']:
                    unique_members[member['id']] = member
            
            players = list(unique_members.values())
            
            if len(players) < 2:
                await status_msg.edit_text("❌ В чате недостаточно участников (нужно минимум 2 человека).")
                return
            
            state.reset()
            state.players = players
            state.total_players = len(players)
            state.is_active = True
            state.start_time = datetime.now()
            # Текст не нужен - только упоминание
            state.text = ""
            
            await status_msg.edit_text(
                f"✅ Тег запущен!\n"
                f"👥 Всего участников: {len(players)}\n"
                f"⏱ Интервал: {TAG_INTERVAL} сек.\n\n"
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
                
                # ТОЛЬКО УПОМИНАНИЕ, без дополнительного текста
                if player['username']:
                    mention = f"@{player['username']}"
                else:
                    mention = f"[{player['full_name']}](tg://user?id={player['id']})"
                
                # Просто упоминание игрока
                message = mention
                
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
                    text=f"✅ **Тег завершен!**\n\n"
                         f"👥 Всего участников: {state.total_players}\n"
                         f"⏱ Время: {duration:.1f} сек.\n"
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
        chat = update.effective_chat
        
        if not await self.is_admin(update, context):
            await update.message.reply_text("❌ Только администраторы могут использовать эту команду!")
            return
        
        state = chat_states[chat.id]
        if state.is_active and not state.paused:
            state.paused = True
            await update.message.reply_text(
                f"⏸ Тег приостановлен!\n"
                f"📊 Прогресс: {state.tagged_count}/{state.total_players}"
            )
        else:
            await update.message.reply_text("❌ Нет активного тега для паузы.")

    async def resume_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        chat = update.effective_chat
        state = chat_states[chat.id]
        
        if not state.is_active:
            await update.message.reply_text("📊 В данный момент нет активного тега.")
            return
        
        status_text = f"""
📊 **Статус тега:**

👥 Всего: {state.total_players}
✅ Протегано: {state.tagged_count}
📈 Осталось: {state.total_players - state.tagged_count}
⏳ Прогресс: {int((state.tagged_count / state.total_players) * 100) if state.total_players > 0 else 0}%

⏱ Статус: {'▶️ Активен' if not state.paused else '⏸ На паузе'}
"""
        await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        logger.info("🚀 Бот TagMaster успешно запущен!")
        logger.info(f"📊 Интервал между тегами: {TAG_INTERVAL} сек.")

    def run(self):
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
        self.application.post_init = self.post_init
        logger.info("Бот запускается...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    bot = TagBot(BOT_TOKEN)
    bot.run()

if __name__ == "__main__":
    main()
