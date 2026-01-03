import telebot
from telebot import types
import os
import logging
from datetime import datetime, timedelta
import sys
import sqlite3
import threading
import time
import re

# ===== НАСТРОЙКИ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8375550237:AAHLnEAmxyclH681zISvAVFQrwBD9u6efdM")
SUPPORT_GROUP_ID = -1003573755326
ADMIN_IDS = [8252849332, 8581498013]  # Добавьте сюда ID администраторов

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# ===== МЕНЕДЖЕР СОСТОЯНИЙ =====
class StateManager:
    def __init__(self):
        self.user_states = {}
        self.user_data = {}
        self.message_history = {}
        
    def set_state(self, user_id, state, data=None):
        self.user_states[user_id] = state
        if data:
            if user_id not in self.user_data:
                self.user_data[user_id] = {}
            self.user_data[user_id].update(data)
    
    def get_state(self, user_id):
        return self.user_states.get(user_id)
    
    def get_data(self, user_id, key=None, default=None):
        if key is None:
            return self.user_data.get(user_id, {})
        return self.user_data.get(user_id, {}).get(key, default)
    
    def clear_state(self, user_id):
        self.user_states.pop(user_id, None)
        self.user_data.pop(user_id, None)
    
    def add_message(self, user_id, message_id, menu_type):
        if user_id not in self.message_history:
            self.message_history[user_id] = []
        self.message_history[user_id].append({
            'message_id': message_id,
            'menu_type': menu_type,
            'timestamp': datetime.now()
        })
        
        # Храним только последние 10 сообщений
        if len(self.message_history[user_id]) > 10:
            self.message_history[user_id] = self.message_history[user_id][-10:]

state = StateManager()

# ===== БАЗА ДАННЫХ =====
class Database:
    def __init__(self):
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        
        # Пользователи
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                tickets_count INTEGER DEFAULT 0
            )
        ''')
        
        # Тикеты
        c.execute('''
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message_id INTEGER,
                group_message_id INTEGER,
                status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2,
                category TEXT DEFAULT 'general',
                subject TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                assigned_to INTEGER,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')
        
        # Сообщения
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER,
                user_id INTEGER,
                direction TEXT,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets(id)
            )
        ''')
        
        # FAQ
        c.execute('''
            CREATE TABLE IF NOT EXISTS faq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT,
                answer TEXT,
                category TEXT,
                sort_order INTEGER DEFAULT 0
            )
        ''')
        
        # Инициализация FAQ
        c.execute("SELECT COUNT(*) FROM faq")
        if c.fetchone()[0] == 0:
            faq_data = [
                ("Как долго ждать ответ?", "Обычно в течение 24 часов. Срочные вопросы - до 10 минут.", "Общее"),
                ("Как создать обращение?", "Просто напишите сообщение боту с описанием проблемы.", "Общее"),
                ("Можно ли прикрепить файлы?", "Да, поддерживаются фото, видео, документы и другие файлы.", "Техническое"),
                ("Как узнать статус обращения?", "Используйте меню 'Мои обращения' или команду /mytickets.", "Общее"),
                ("Что делать, если проблема не решена?", "Ответьте на последнее сообщение от поддержки с уточнениями.", "Общее"),
                ("Как отменить обращение?", "В данный момент отмена не предусмотрена, но можно просто не отвечать.", "Общее"),
                ("Кто видит мои сообщения?", "Только сотрудники поддержки в закрытой группе.", "Безопасность"),
                ("Можно ли анонимно обратиться?", "Ваш ID виден поддержке, но можно не указывать имя.", "Безопасность"),
            ]
            c.executemany(
                "INSERT INTO faq (question, answer, category) VALUES (?, ?, ?)",
                faq_data
            )
        
        conn.commit()
        conn.close()
    
    def get_user(self, user_id):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        conn.close()
        return user
    
    def create_user(self, user_id, username, first_name, last_name):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        ''', (user_id, username, first_name, last_name))
        conn.commit()
        conn.close()
    
    def create_ticket(self, user_id, message_id, group_message_id, subject="", category="general", priority=2):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        
        c.execute('''
            INSERT INTO tickets (user_id, message_id, group_message_id, subject, category, priority)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, message_id, group_message_id, subject, category, priority))
        
        ticket_id = c.lastrowid
        
        # Увеличиваем счетчик тикетов пользователя
        c.execute('''
            UPDATE users SET tickets_count = tickets_count + 1 
            WHERE user_id = ?
        ''', (user_id,))
        
        conn.commit()
        conn.close()
        return ticket_id
    
    def get_ticket(self, ticket_id):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
        ticket = c.fetchone()
        conn.close()
        return ticket
    
    def get_ticket_by_group_message(self, group_message_id):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute("SELECT * FROM tickets WHERE group_message_id = ?", (group_message_id,))
        ticket = c.fetchone()
        conn.close()
        return ticket
    
    def get_user_tickets(self, user_id, limit=10):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute('''
            SELECT * FROM tickets 
            WHERE user_id = ? 
            ORDER BY created_at DESC 
            LIMIT ?
        ''', (user_id, limit))
        tickets = c.fetchall()
        conn.close()
        return tickets
    
    def update_ticket_status(self, ticket_id, status, assigned_to=None):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        
        if assigned_to:
            c.execute('''
                UPDATE tickets 
                SET status = ?, assigned_to = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, assigned_to, ticket_id))
        else:
            c.execute('''
                UPDATE tickets 
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (status, ticket_id))
        
        conn.commit()
        conn.close()
    
    def get_faq_categories(self):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute("SELECT DISTINCT category FROM faq ORDER BY category")
        categories = [row[0] for row in c.fetchall()]
        conn.close()
        return categories
    
    def get_faq_by_category(self, category):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute('''
            SELECT id, question, answer 
            FROM faq 
            WHERE category = ? 
            ORDER BY sort_order, question
        ''', (category,))
        faq = c.fetchall()
        conn.close()
        return faq
    
    def add_message(self, ticket_id, user_id, direction, content):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute('''
            INSERT INTO messages (ticket_id, user_id, direction, content)
            VALUES (?, ?, ?, ?)
        ''', (ticket_id, user_id, direction, content[:500]))
        conn.commit()
        conn.close()

db = Database()

# ===== УТИЛИТЫ =====
def get_user_display_name(user):
    name_parts = []
    if user.first_name:
        name_parts.append(user.first_name)
    if user.last_name:
        name_parts.append(user.last_name)
    
    if name_parts:
        display_name = ' '.join(name_parts)
        if user.username:
            display_name += f" (@{user.username})"
    elif user.username:
        display_name = f"@{user.username}"
    else:
        display_name = f"User_{user.id}"
    
    return display_name

def clean_text(text, max_length=1500):
    if not text:
        return ""
    text = ' '.join(text.strip().split())
    if len(text) > max_length:
        text = text[:max_length-3] + "..."
    return text

def format_time(dt=None):
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%H:%M")

def format_date(dt=None):
    if dt is None:
        dt = datetime.now()
    return dt.strftime("%d.%m.%Y")

def create_inline_keyboard(buttons, row_width=2):
    markup = types.InlineKeyboardMarkup(row_width=row_width)
    for row in buttons:
        row_buttons = []
        for text, callback_data in row:
            row_buttons.append(types.InlineKeyboardButton(text, callback_data=callback_data))
        markup.add(*row_buttons)
    return markup

def detect_priority_category(text):
    text_lower = text.lower() if text else ""
    
    # Приоритет
    priority = 2  # Средний
    
    critical_words = ['срочно', 'критично', 'не работает', 'ошибка', 'баг', 'сломал', 'падает']
    high_words = ['важно', 'проблема', 'нужна помощь', 'помогите', 'не могу']
    low_words = ['вопрос', 'интересно', 'подскажите', 'любопытно', 'не срочно']
    
    if any(word in text_lower for word in critical_words):
        priority = 4
    elif any(word in text_lower for word in high_words):
        priority = 3
    elif any(word in text_lower for word in low_words):
        priority = 1
    
    # Категория
    category = "general"
    
    if any(word in text_lower for word in ['оплат', 'деньг', 'плат', 'биллинг', 'тариф']):
        category = "billing"
    elif any(word in text_lower for word in ['ошибк', 'баг', 'глюк', 'не работ', 'сломал']):
        category = "technical"
    elif any(word in text_lower for word in ['предложен', 'идея', 'функци', 'улучшен']):
        category = "suggestion"
    elif any(word in text_lower for word in ['жалоб', 'претензи', 'недовол']):
        category = "complaint"
    
    return priority, category

# ===== МЕНЮ =====
class MenuManager:
    @staticmethod
    def main_menu():
        text = """<b>🛠️ Служба поддержки</b>

Добро пожаловать! Я помогу вам связаться с нашей службой поддержки.

<code>━━━━━━━━━━━━━━</code>

<b>📋 Доступные действия:</b>
• 📩 Создать обращение
• 📋 FAQ и частые вопросы
• 📊 Мои обращения
• 💭 Оставить отзыв
• ℹ️ Справка

<code>━━━━━━━━━━━━━━</code>

<i>Выберите действие:</i>"""
        
        buttons = [
            [("📩 Создать обращение", "create_ticket")],
            [("📋 FAQ", "show_faq"), ("📊 Мои обращения", "my_tickets")],
            [("💭 Отзыв", "feedback"), ("ℹ️ Справка", "help")]
        ]
        
        return text, create_inline_keyboard(buttons)
    
    @staticmethod
    def help_menu():
        text = """<b>ℹ️ Справка</b>

<code>━━━━━━━━━━━━━━</code>

<b>Как пользоваться ботом:</b>

1️⃣ <b>Создание обращения</b>
   • Нажмите "Создать обращение"
   • Опишите проблему подробно
   • При необходимости прикрепите файлы

2️⃣ <b>Отслеживание обращений</b>
   • Используйте "Мои обращения"
   • Смотрите статус каждого обращения
   • Отвечайте на сообщения поддержки

3️⃣ <b>FAQ</b>
   • Ответы на частые вопросы
   • Разделены по категориям
   • Постоянно обновляются

<code>━━━━━━━━━━━━━━</code>

<b>Основные команды:</b>
• /start - перезапуск бота
• /help - эта справка
• /mytickets - мои обращения
• /faq - частые вопросы
• /feedback - оставить отзыв
• /cancel - отмена текущей операции

<code>━━━━━━━━━━━━━━</code>

<b>Команды для администраторов:</b>
• /rass - массовая рассылка

<code>━━━━━━━━━━━━━━</code>

<i>⏱️ Время ответа: до 24 часов</i>"""
        
        buttons = [
            [("📩 Создать обращение", "create_ticket")],
            [("📋 FAQ", "show_faq"), ("📊 Мои обращения", "my_tickets")],
            [("🔙 Назад", "main_menu")]
        ]
        
        return text, create_inline_keyboard(buttons)
    
    @staticmethod
    def faq_menu():
        categories = db.get_faq_categories()
        
        text = """<b>📋 Частые вопросы (FAQ)</b>

<code>━━━━━━━━━━━━━━</code>

Выберите категорию:"""
        
        buttons = []
        for category in categories:
            buttons.append([(f"📁 {category}", f"faq_cat:{category}")])
        
        buttons.append([("🔙 Назад", "main_menu")])
        
        return text, create_inline_keyboard(buttons)
    
    @staticmethod
    def faq_category_menu(category):
        faq_items = db.get_faq_by_category(category)
        
        text = f"""<b>📋 FAQ: {category}</b>

<code>━━━━━━━━━━━━━━</code>

Выберите вопрос:"""
        
        buttons = []
        for faq_id, question, _ in faq_items:
            short_question = question[:30] + "..." if len(question) > 30 else question
            buttons.append([(f"❓ {short_question}", f"faq_item:{faq_id}")])
        
        buttons.append([("🔙 Назад к категориям", "show_faq")])
        
        return text, create_inline_keyboard(buttons)
    
    @staticmethod
    def faq_item_menu(faq_id):
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        c.execute("SELECT question, answer, category FROM faq WHERE id = ?", (faq_id,))
        question, answer, category = c.fetchone()
        conn.close()
        
        text = f"""<b>❓ {question}</b>

<code>━━━━━━━━━━━━━━</code>

{answer}

<code>━━━━━━━━━━━━━━</code>

<i>Категория: {category}</i>"""
        
        buttons = [
            [("🔙 Назад к вопросам", f"faq_cat:{category}")],
            [("📋 Все категории", "show_faq")]
        ]
        
        return text, create_inline_keyboard(buttons)
    
    @staticmethod
    def my_tickets_menu(user_id):
        tickets = db.get_user_tickets(user_id, limit=5)
        
        if not tickets:
            text = """<b>📭 Мои обращения</b>

<code>━━━━━━━━━━━━━━</code>

У вас еще нет обращений в поддержку.

Хотите создать новое обращение?"""
            
            buttons = [
                [("📩 Создать обращение", "create_ticket")],
                [("🔙 Назад", "main_menu")]
            ]
        else:
            text = """<b>📊 Мои обращения</b>

<code>━━━━━━━━━━━━━━</code>

Последние обращения:"""
            
            for ticket in tickets[:3]:
                ticket_id, _, _, _, status, priority, category, subject, created_at, _, assigned_to = ticket
                
                status_icon = {
                    'open': '🟡',
                    'in_progress': '🟠', 
                    'resolved': '🟢',
                    'closed': '⚫'
                }.get(status, '⚪')
                
                created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                created_str = created.strftime("%d.%m %H:%M")
                
                text += f"""
{status_icon} <b>#{ticket_id}</b> • {created_str}
📝 {subject[:50] if subject else 'Без темы'}"""
            
            if len(tickets) > 3:
                text += f"\n\n<i>... и еще {len(tickets) - 3} обращений</i>"
            
            text += "\n<code>━━━━━━━━━━━━━━</code>"
            
            buttons = [
                [("📝 Все обращения", "all_tickets")],
                [("📩 Новое обращение", "create_ticket"), ("🔙 Назад", "main_menu")]
            ]
        
        return text, create_inline_keyboard(buttons)
    
    @staticmethod 
    def all_tickets_menu(user_id, page=0):
        tickets = db.get_user_tickets(user_id, limit=100)
        items_per_page = 5
        total_pages = max(1, (len(tickets) + items_per_page - 1) // items_per_page)
        start_idx = page * items_per_page
        page_tickets = tickets[start_idx:start_idx + items_per_page]
        
        text = f"""<b>📊 Все обращения</b>

<code>━━━━━━━━━━━━━━</code>

Страница {page + 1} из {total_pages}:"""
        
        if not page_tickets:
            text += "\n\nНа этой странице нет обращений."
        else:
            for ticket in page_tickets:
                ticket_id, _, _, _, status, _, _, subject, created_at, _, _ = ticket
                
                status_icon = {
                    'open': '🟡',
                    'in_progress': '🟠',
                    'resolved': '🟢',
                    'closed': '⚫'
                }.get(status, '⚪')
                
                created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                created_str = created.strftime("%d.%m.%Y")
                
                text += f"""
{status_icon} <b>#{ticket_id}</b> • {created_str}
📝 {subject[:40] if subject else 'Без темы'}"""
        
        buttons = []
        
        # Пагинация
        nav_buttons = []
        if page > 0:
            nav_buttons.append(("◀️", f"tickets_page:{page-1}"))
        
        nav_buttons.append((f"{page+1}/{total_pages}", "noop"))
        
        if page < total_pages - 1:
            nav_buttons.append(("▶️", f"tickets_page:{page+1}"))
        
        if nav_buttons:
            buttons.append(nav_buttons)
        
        buttons.append([("📊 Мои обращения", "my_tickets"), ("🔙 Главная", "main_menu")])
        
        return text, create_inline_keyboard(buttons)
    
    @staticmethod
    def feedback_menu():
        text = """<b>💭 Оставить отзыв</b>

<code>━━━━━━━━━━━━━━</code>

Пожалуйста, напишите ваш отзыв о работе нашей поддержки.

Вы можете оценить:
• Скорость ответа
• Качество решения
• Вежливость сотрудников
• Предложения по улучшению

<code>━━━━━━━━━━━━━━</code>

<i>Просто отправьте текстовое сообщение с вашим отзывом.
Или нажмите "Отмена" для возврата в меню.</i>"""
        
        buttons = [
            [("❌ Отмена", "main_menu")]
        ]
        
        return text, create_inline_keyboard(buttons)
    
    @staticmethod
    def create_ticket_menu():
        text = """<b>📩 Создание обращения</b>

<code>━━━━━━━━━━━━━━</code>

Опишите вашу проблему или вопрос максимально подробно:

• Что произошло?
• Каковы ожидаемые результаты?
• Какие шаги вы предприняли?
• Есть ли ошибки или скриншоты?

<code>━━━━━━━━━━━━━━</code>

<i>Просто отправьте текстовое сообщение с описанием проблемы.
Вы также можете прикрепить фото, видео или документы.</i>

<i>Или нажмите "Отмена" для возврата в меню.</i>"""
        
        buttons = [
            [("❌ Отмена", "main_menu")]
        ]
        
        return text, create_inline_keyboard(buttons)

# ===== КОМАНДЫ =====
@bot.message_handler(commands=['start'])
def start_command(message):
    user_id = message.from_user.id
    user = message.from_user
    
    db.create_user(user_id, user.username, user.first_name, user.last_name)
    
    text, markup = MenuManager.main_menu()
    sent_msg = bot.send_message(user_id, text, reply_markup=markup)
    state.add_message(user_id, sent_msg.message_id, "main_menu")
    state.clear_state(user_id)

@bot.message_handler(commands=['help'])
def help_command(message):
    user_id = message.from_user.id
    text, markup = MenuManager.help_menu()
    sent_msg = bot.send_message(user_id, text, reply_markup=markup)
    state.add_message(user_id, sent_msg.message_id, "help_menu")

@bot.message_handler(commands=['mytickets'])
def mytickets_command(message):
    user_id = message.from_user.id
    text, markup = MenuManager.my_tickets_menu(user_id)
    sent_msg = bot.send_message(user_id, text, reply_markup=markup)
    state.add_message(user_id, sent_msg.message_id, "my_tickets_menu")

@bot.message_handler(commands=['faq'])
def faq_command(message):
    user_id = message.from_user.id
    text, markup = MenuManager.faq_menu()
    sent_msg = bot.send_message(user_id, text, reply_markup=markup)
    state.add_message(user_id, sent_msg.message_id, "faq_menu")

@bot.message_handler(commands=['feedback'])
def feedback_command(message):
    user_id = message.from_user.id
    text, markup = MenuManager.feedback_menu()
    sent_msg = bot.send_message(user_id, text, reply_markup=markup)
    state.add_message(user_id, sent_msg.message_id, "feedback_menu")
    state.set_state(user_id, "waiting_feedback")

@bot.message_handler(commands=['rass'])
def broadcast_command(message):
    user_id = message.from_user.id
    
    # Проверка на администратора
    if user_id not in ADMIN_IDS:
        bot.send_message(user_id, "❌ Эта команда доступна только администраторам.")
        return
    
    # Устанавливаем состояние для ожидания текста рассылки
    state.set_state(user_id, "waiting_broadcast")
    
    text = """<b>📢 Массовая рассылка</b>

<code>━━━━━━━━━━━━━━</code>

Вы можете отправить сообщение всем пользователям, которые когда-либо взаимодействовали с ботом.

<b>Формат сообщения:</b>
• Текст (обязательно)
• Фото/документ/видео (опционально)

<code>━━━━━━━━━━━━━━</code>

<i>Отправьте сообщение для рассылки.
Или отправьте /cancel для отмены.</i>"""
    
    buttons = [
        [("❌ Отмена", "main_menu")]
    ]
    
    sent_msg = bot.send_message(
        user_id, 
        text, 
        reply_markup=create_inline_keyboard(buttons)
    )
    state.add_message(user_id, sent_msg.message_id, "broadcast_menu")

@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    user_id = message.from_user.id
    user_state = state.get_state(user_id)
    
    if user_state == "waiting_broadcast":
        bot.send_message(user_id, "✅ Рассылка отменена.")
        state.clear_state(user_id)
        
        # Возвращаем в главное меню
        text, markup = MenuManager.main_menu()
        sent_msg = bot.send_message(user_id, text, reply_markup=markup)
        state.add_message(user_id, sent_msg.message_id, "main_menu")
    else:
        bot.send_message(user_id, "❌ Нет активных команд для отмены.")

# ===== CALLBACK ОБРАБОТКА =====
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    message_id = call.message.message_id
    
    try:
        if call.data == "noop":
            bot.answer_callback_query(call.id)
            return
            
        elif call.data == "main_menu":
            # Также сбрасываем состояние рассылки, если оно было
            if state.get_state(call.from_user.id) == "waiting_broadcast":
                state.clear_state(call.from_user.id)
            
            text, markup = MenuManager.main_menu()
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, "main_menu")
            state.clear_state(user_id)
            
        elif call.data == "help":
            text, markup = MenuManager.help_menu()
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, "help_menu")
            
        elif call.data == "show_faq":
            text, markup = MenuManager.faq_menu()
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, "faq_menu")
            
        elif call.data.startswith("faq_cat:"):
            category = call.data.split(":", 1)[1]
            text, markup = MenuManager.faq_category_menu(category)
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, f"faq_cat:{category}")
            
        elif call.data.startswith("faq_item:"):
            faq_id = int(call.data.split(":", 1)[1])
            text, markup = MenuManager.faq_item_menu(faq_id)
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, f"faq_item:{faq_id}")
            
        elif call.data == "my_tickets":
            text, markup = MenuManager.my_tickets_menu(user_id)
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, "my_tickets_menu")
            
        elif call.data == "all_tickets":
            text, markup = MenuManager.all_tickets_menu(user_id, 0)
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, "all_tickets:0")
            
        elif call.data.startswith("tickets_page:"):
            page = int(call.data.split(":", 1)[1])
            text, markup = MenuManager.all_tickets_menu(user_id, page)
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, f"all_tickets:{page}")
            
        elif call.data == "feedback":
            text, markup = MenuManager.feedback_menu()
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, "feedback_menu")
            state.set_state(user_id, "waiting_feedback")
            
        elif call.data == "create_ticket":
            text, markup = MenuManager.create_ticket_menu()
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(user_id, message_id, "create_ticket_menu")
            state.set_state(user_id, "creating_ticket")
            
        elif call.data.startswith("support_"):
            handle_support_callback(call)
            return
            
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        logger.error(f"Callback error: {e}")
        try:
            bot.answer_callback_query(call.id, "⚠️ Ошибка. Попробуйте еще раз.")
        except:
            pass

# ===== ОБРАБОТКА СООБЩЕНИЙ =====
@bot.message_handler(func=lambda message: message.chat.type == 'private' and not message.text.startswith('/'))
def private_message_handler(message):
    user_id = message.from_user.id
    user_state = state.get_state(user_id)
    
    if user_state == "waiting_feedback":
        handle_feedback(message)
    elif user_state == "creating_ticket" or not user_state:
        handle_ticket_creation(message)
    elif user_state == "waiting_broadcast":
        handle_broadcast(message)

def handle_feedback(message):
    user_id = message.from_user.id
    feedback_text = message.text or message.caption or ""
    
    if not feedback_text.strip():
        bot.send_message(user_id, "❌ Отзыв не может быть пустым.")
        return
    
    user = message.from_user
    user_name = get_user_display_name(user)
    
    # Отправляем в группу поддержки
    feedback_msg = f"""<b>💭 Новый отзыв</b>

<code>━━━━━━━━━━━━━━</code>

<b>👤 От:</b> {user_name}
<b>🆔 ID:</b> <code>{user_id}</code>
<b>🕐 Время:</b> {format_time()} • {format_date()}

<code>━━━━━━━━━━━━━━</code>

{clean_text(feedback_text)}

<code>━━━━━━━━━━━━━━</code>
<i>Обратная связь от пользователя</i>"""
    
    try:
        bot.send_message(SUPPORT_GROUP_ID, feedback_msg, parse_mode="HTML")
        
        # Подтверждение пользователю
        bot.send_message(user_id, "✅ Спасибо за ваш отзыв! Он поможет нам стать лучше.")
        
        # Возвращаем в главное меню
        text, markup = MenuManager.main_menu()
        sent_msg = bot.send_message(user_id, text, reply_markup=markup)
        state.add_message(user_id, sent_msg.message_id, "main_menu")
        state.clear_state(user_id)
        
    except Exception as e:
        logger.error(f"Feedback error: {e}")
        bot.send_message(user_id, "❌ Не удалось отправить отзыв. Попробуйте позже.")

def handle_broadcast(message):
    user_id = message.from_user.id
    
    # Проверка на администратора
    if user_id not in ADMIN_IDS:
        bot.send_message(user_id, "❌ Эта команда доступна только администраторам.")
        return
    
    # Получаем текст или caption
    text_content = message.text or message.caption or ""
    
    if not text_content and not (message.photo or message.document or message.video):
        bot.send_message(user_id, "❌ Сообщение не может быть пустым. Пожалуйста, добавьте текст.")
        return
    
    # Сначала отправляем подтверждение администратору
    bot.send_message(user_id, "⏳ Начинаю рассылку...")
    
    # Получаем всех пользователей из базы данных
    conn = sqlite3.connect('support.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    
    total_users = len(users)
    success_count = 0
    failed_count = 0
    
    # Статистика для администратора
    stats_msg = bot.send_message(
        user_id, 
        f"📊 Статистика рассылки:\n"
        f"• Всего пользователей: {total_users}\n"
        f"• Успешно отправлено: 0\n"
        f"• Не удалось отправить: 0\n"
        f"⏳ Отправка..."
    )
    
    # Отправляем сообщение каждому пользователю
    for user_row in users:
        target_user_id = user_row[0]
        
        # Пропускаем самого администратора, если нужно
        if target_user_id == user_id:
            success_count += 1
            continue
        
        try:
            if message.text:
                # Текстовое сообщение
                bot.send_message(target_user_id, text_content)
            elif message.photo:
                # Фото с текстом
                photo_id = message.photo[-1].file_id
                bot.send_photo(
                    target_user_id, 
                    photo_id, 
                    caption=text_content if text_content else None
                )
            elif message.document:
                # Документ с текстом
                doc_id = message.document.file_id
                bot.send_document(
                    target_user_id, 
                    doc_id, 
                    caption=text_content if text_content else None
                )
            elif message.video:
                # Видео с текстом
                video_id = message.video.file_id
                bot.send_video(
                    target_user_id, 
                    video_id, 
                    caption=text_content if text_content else None
                )
            
            success_count += 1
            
            # Обновляем статистику каждые 10 отправок
            if success_count % 10 == 0:
                try:
                    bot.edit_message_text(
                        f"📊 Статистика рассылки:\n"
                        f"• Всего пользователей: {total_users}\n"
                        f"• Успешно отправлено: {success_count}\n"
                        f"• Не удалось отправить: {failed_count}\n"
                        f"⏳ Отправка... ({success_count + failed_count}/{total_users})",
                        chat_id=user_id,
                        message_id=stats_msg.message_id
                    )
                except:
                    pass
            
            # Небольшая задержка, чтобы не превысить лимиты Telegram
            time.sleep(0.1)
            
        except Exception as e:
            failed_count += 1
            logger.error(f"Broadcast to {target_user_id} failed: {e}")
            # Пропускаем ошибки и продолжаем рассылку
    
    # Финальное сообщение с результатами
    final_text = f"""<b>✅ Рассылка завершена</b>

<code>━━━━━━━━━━━━━━</code>

📊 <b>Результаты:</b>
• Всего пользователей: {total_users}
• Успешно отправлено: {success_count}
• Не удалось отправить: {failed_count}

<code>━━━━━━━━━━━━━━</code>

<i>Процент доставки: {round(success_count/total_users*100 if total_users > 0 else 0, 2)}%</i>"""
    
    try:
        bot.edit_message_text(
            final_text,
            chat_id=user_id,
            message_id=stats_msg.message_id,
            parse_mode="HTML"
        )
    except:
        bot.send_message(user_id, final_text, parse_mode="HTML")
    
    # Возвращаем в главное меню
    text, markup = MenuManager.main_menu()
    sent_menu_msg = bot.send_message(user_id, text, reply_markup=markup)
    state.add_message(user_id, sent_menu_msg.message_id, "main_menu")
    state.clear_state(user_id)

def handle_ticket_creation(message):
    user_id = message.from_user.id
    user = message.from_user
    
    db.create_user(user_id, user.username, user.first_name, user.last_name)
    
    text_content = message.text or message.caption or ""
    
    if not text_content and not (message.photo or message.document or message.video):
        bot.send_message(user_id, "❌ Сообщение не может быть пустым. Пожалуйста, опишите проблему.")
        return
    
    # Определяем приоритет и категорию
    priority, category = detect_priority_category(text_content)
    
    # Создаем тему
    subject = text_content[:100].strip() if text_content else "Без темы"
    if not subject:
        subject = "Файловое обращение"
    
    # Приоритет текст
    priority_text = {
        1: "📌 Низкий",
        2: "📝 Средний",
        3: "⚠️ Высокий",
        4: "🚨 Критичный"
    }.get(priority, "📝 Средний")
    
    # Подтверждение пользователю
    confirm_text = f"""<b>✅ Обращение создано</b>

<code>━━━━━━━━━━━━━━</code>

Ваше обращение получено и отправлено в поддержку.

<b>📊 Детали:</b>
• Приоритет: {priority_text}
• Категория: {category}
• Время: {format_time()} • {format_date()}

<code>━━━━━━━━━━━━━━</code>

<i>Ответ придет в этот же чат. Вы можете отвечать на сообщения поддержки для продолжения диалога.</i>"""
    
    try:
        bot.send_message(user_id, confirm_text)
        
        # Формируем сообщение для группы
        user_name = get_user_display_name(user)
        group_message = format_group_message(user_id, user_name, message, priority, category, subject)
        
        # Inline кнопки для поддержки
        markup = create_inline_keyboard([
            [("✅ Взять в работу", f"support_take:{user_id}")],
            [("📋 Подробнее", f"support_details:{user_id}"), ("❌ Отклонить", f"support_reject:{user_id}")]
        ])
        
        # Отправляем в группу
        if message.text:
            sent_msg = bot.send_message(
                SUPPORT_GROUP_ID,
                group_message,
                parse_mode="HTML",
                reply_markup=markup
            )
        elif message.photo:
            photo_id = message.photo[-1].file_id
            sent_msg = bot.send_photo(
                SUPPORT_GROUP_ID,
                photo_id,
                caption=group_message,
                parse_mode="HTML",
                reply_markup=markup
            )
        elif message.document:
            doc_id = message.document.file_id
            sent_msg = bot.send_document(
                SUPPORT_GROUP_ID,
                doc_id,
                caption=group_message,
                parse_mode="HTML",
                reply_markup=markup
            )
        elif message.video:
            video_id = message.video.file_id
            sent_msg = bot.send_video(
                SUPPORT_GROUP_ID,
                video_id,
                caption=group_message,
                parse_mode="HTML",
                reply_markup=markup
            )
        
        # Создаем тикет в БД
        ticket_id = db.create_ticket(
            user_id,
            message.message_id,
            sent_msg.message_id,
            subject,
            category,
            priority
        )
        
        # Добавляем первое сообщение
        db.add_message(ticket_id, user_id, "user_to_support", text_content[:500])
        
        # Возвращаем в главное меню
        text, markup = MenuManager.main_menu()
        sent_menu_msg = bot.send_message(user_id, text, reply_markup=markup)
        state.add_message(user_id, sent_menu_msg.message_id, "main_menu")
        state.clear_state(user_id)
        
        logger.info(f"Created ticket #{ticket_id} from user {user_id}")
        
    except Exception as e:
        logger.error(f"Ticket creation error: {e}")
        bot.send_message(user_id, "❌ Не удалось создать обращение. Попробуйте позже.")

def format_group_message(user_id, user_name, message, priority, category, subject):
    priority_text = {
        1: "📌 НИЗКИЙ",
        2: "📝 СРЕДНИЙ",
        3: "⚠️ ВЫСОКИЙ",
        4: "🚨 КРИТИЧНЫЙ"
    }.get(priority, "📝 СРЕДНИЙ")
    
    text_content = message.text or message.caption or ""
    
    message_text = f"""<b>📩 НОВОЕ ОБРАЩЕНИЕ [{priority_text}]</b>

<code>━━━━━━━━━━━━━━</code>

<b>👤 Пользователь:</b> {user_name}
<b>🆔 ID:</b> <code>{user_id}</code>
<b>🏷️ Категория:</b> {category}
<b>📝 Тема:</b> {subject}
<b>🕐 Время:</b> {format_time()} • {format_date()}

<code>━━━━━━━━━━━━━━</code>"""
    
    if text_content:
        message_text += f"\n<b>📝 Сообщение:</b>\n{clean_text(text_content, 1000)}"
    
    message_text += f"""

<code>━━━━━━━━━━━━━━</code>

<i>Ответьте на это сообщение, чтобы отправить ответ пользователю</i>"""
    
    return message_text

# ===== ОТВЕТЫ В ГРУППЕ =====
@bot.message_handler(func=lambda m: m.chat.id == SUPPORT_GROUP_ID and m.reply_to_message)
def group_reply_handler(message):
    if message.from_user.is_bot:
        return
    
    replied_msg = message.reply_to_message
    
    # Ищем тикет
    ticket = db.get_ticket_by_group_message(replied_msg.message_id)
    
    if not ticket:
        # Пробуем найти ID пользователя в тексте
        match = re.search(r'ID:</b> <code>(\d+)</code>', replied_msg.text or replied_msg.caption or "")
        if match:
            user_id = int(match.group(1))
            tickets = db.get_user_tickets(user_id, limit=1)
            if tickets:
                ticket = tickets[0]
    
    if ticket:
        send_response_to_user(ticket[1], ticket[0], message)
        
        # Обновляем статус
        db.update_ticket_status(ticket[0], "in_progress", message.from_user.id)
        
        # Уведомление в группе
        bot.send_message(
            SUPPORT_GROUP_ID,
            f"✅ <b>Ответ отправлен</b>\n"
            f"👤 Пользователю: <code>{ticket[1]}</code>\n"
            f"🎯 Тикет: #{ticket[0]}\n"
            f"👨‍💼 От: {get_user_display_name(message.from_user)}\n"
            f"🕐 {format_time()}",
            reply_to_message_id=message.message_id
        )

def send_response_to_user(user_id, ticket_id, message):
    try:
        response_text = f"""<b>📨 Ответ от поддержки</b>

<code>━━━━━━━━━━━━━━</code>

Обращение: <b>#{ticket_id}</b>
Время: <b>{format_time()}</b>

<code>━━━━━━━━━━━━━━</code>
"""
        
        text_content = message.text or message.caption or ""
        
        if message.text:
            full_text = response_text + clean_text(text_content)
            bot.send_message(user_id, full_text)
            
        elif message.photo:
            photo_id = message.photo[-1].file_id
            caption = response_text + clean_text(text_content)
            bot.send_photo(user_id, photo_id, caption=caption)
            
        elif message.document:
            doc_id = message.document.file_id
            caption = response_text + clean_text(text_content)
            bot.send_document(user_id, doc_id, caption=caption)
            
        elif message.video:
            video_id = message.video.file_id
            caption = response_text + clean_text(text_content)
            bot.send_video(user_id, video_id, caption=caption)
        
        # Сохраняем сообщение
        db.add_message(ticket_id, message.from_user.id, "support_to_user", text_content[:500])
        
    except Exception as e:
        error_msg = f"""<b>❌ Ошибка отправки</b>

Не удалось отправить ответ пользователю <code>{user_id}</code>.

<b>Причина:</b> {str(e)}"""
        
        bot.send_message(
            SUPPORT_GROUP_ID,
            error_msg,
            reply_to_message_id=message.message_id
        )
        logger.error(f"Send response error: {e}")

# ===== КНОПКИ ПОДДЕРЖКИ =====
def handle_support_callback(call):
    message_id = call.message.message_id
    
    if call.data.startswith("support_take:"):
        user_id = int(call.data.split(":")[1])
        ticket = db.get_ticket_by_group_message(message_id)
        
        if ticket:
            db.update_ticket_status(ticket[0], "in_progress", call.from_user.id)
            
            # Редактируем сообщение
            original_text = call.message.text or call.message.caption or ""
            edited_text = original_text + f"\n\n✅ <b>Взято в работу</b> @{call.from_user.username}"
            
            try:
                if call.message.text:
                    bot.edit_message_text(
                        edited_text,
                        chat_id=call.message.chat.id,
                        message_id=message_id,
                        parse_mode="HTML"
                    )
                else:
                    bot.edit_message_caption(
                        edited_text,
                        chat_id=call.message.chat.id,
                        message_id=message_id,
                        parse_mode="HTML"
                    )
                
                # Уведомляем пользователя
                try:
                    bot.send_message(
                        user_id,
                        f"👨‍💼 <b>Ваше обращение взято в работу</b>\n\n"
                        f"Специалист @{call.from_user.username} начал работу над вашим вопросом."
                    )
                except:
                    pass
                
            except Exception as e:
                logger.error(f"Edit message error: {e}")
        
        bot.answer_callback_query(call.id, "✅ Взято в работу")
    
    elif call.data.startswith("support_details:"):
        user_id = int(call.data.split(":")[1])
        user = db.get_user(user_id)
        
        if user:
            details = f"""
<b>📋 Информация о пользователе:</b>

👤 Имя: {user[2] or 'Не указано'} {user[3] or ''}
📱 Username: @{user[1] or 'отсутствует'}
🆔 ID: <code>{user[0]}</code>
📅 Зарегистрирован: {user[4]}
📊 Обращений: {user[5]}
"""
            bot.answer_callback_query(call.id, details, show_alert=True)
        else:
            bot.answer_callback_query(call.id, "Пользователь не найден")
    
    elif call.data.startswith("support_reject:"):
        user_id = int(call.data.split(":")[1])
        ticket = db.get_ticket_by_group_message(message_id)
        
        if ticket:
            db.update_ticket_status(ticket[0], "closed")
            
            original_text = call.message.text or call.message.caption or ""
            edited_text = original_text + f"\n\n❌ <b>Отклонено</b> @{call.from_user.username}"
            
            try:
                if call.message.text:
                    bot.edit_message_text(
                        edited_text,
                        chat_id=call.message.chat.id,
                        message_id=message_id,
                        parse_mode="HTML"
                    )
                else:
                    bot.edit_message_caption(
                        edited_text,
                        chat_id=call.message.chat.id,
                        message_id=message_id,
                        parse_mode="HTML"
                    )
            except Exception as e:
                logger.error(f"Edit message error: {e}")
        
        bot.answer_callback_query(call.id, "❌ Обращение отклонено")

# ===== ОЧИСТКА ДАННЫХ =====
def cleanup_old_data():
    """Очистка старых данных"""
    while True:
        try:
            conn = sqlite3.connect('support.db')
            c = conn.cursor()
            
            # Удаляем закрытые тикеты старше 30 дней
            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("DELETE FROM tickets WHERE status = 'closed' AND updated_at < ?", (cutoff,))
            
            deleted = c.rowcount
            if deleted > 0:
                logger.info(f"Удалено {deleted} старых тикетов")
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        
        time.sleep(3600)  # Каждый час

# ===== ЗАПУСК =====
if __name__ == "__main__":
    print("=" * 60)
    print("🤖 УМНЫЙ БОТ ПОДДЕРЖКИ")
    print("=" * 60)
    print(f"Токен: {'✅' if TOKEN else '❌'}")
    print(f"Группа: {SUPPORT_GROUP_ID}")
    print("=" * 60)
    print("📋 Функции:")
    print("• 🚫 Без клавиатурных кнопок")
    print("• 🔄 Авторедактирование сообщений")
    print("• 🔙 Корректные кнопки 'Назад'")
    print("• 📁 Иерархическое меню")
    print("• 📊 История обращений")
    print("• ❓ FAQ с категориями")
    print("• 💭 Система отзывов")
    print("• ⚡ Быстрые действия для поддержки")
    print("• 📢 Массовая рассылка (только для админов)")
    print("=" * 60)
    print("🚀 Запуск...")
    
    # Запуск фоновой очистки
    cleanup_thread = threading.Thread(target=cleanup_old_data, daemon=True)
    cleanup_thread.start()
    
    try:
        bot.infinity_polling(timeout=60)
    except KeyboardInterrupt:
        print("\n🛑 Остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        time.sleep(30)
        os.execv(sys.executable, ['python'] + sys.argv)