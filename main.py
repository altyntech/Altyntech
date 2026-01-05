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

# ===== МАССОВАЯ РАССЫЛКА =====
class BroadcastManager:
    def __init__(self):
        self.active_broadcasts = {}
        self.broadcast_stats = {}
        
    def get_all_users(self):
        """Получить всех пользователей из базы данных"""
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        try:
            c.execute("SELECT user_id FROM users ORDER BY user_id")
            users = [row[0] for row in c.fetchall()]
            return users
        except Exception as e:
            logger.error(f"Get users error: {e}")
            return []
        finally:
            conn.close()
    
    def send_broadcast(self, admin_id, message):
        """Запустить массовую рассылку"""
        broadcast_id = f"broadcast_{int(time.time())}"
        
        # Сохраняем информацию о рассылке
        self.active_broadcasts[broadcast_id] = {
            'admin_id': admin_id,
            'message': message,
            'start_time': datetime.now(),
            'status': 'running',
            'sent': 0,
            'failed': 0,
            'total': 0,
            'current': 0
        }
        
        # Запускаем в отдельном потоке
        thread = threading.Thread(
            target=self._run_broadcast,
            args=(broadcast_id, message),
            daemon=True
        )
        thread.start()
        
        return broadcast_id
    
    def _run_broadcast(self, broadcast_id, message):
        """Выполнить рассылку в фоновом режиме"""
        broadcast = self.active_broadcasts[broadcast_id]
        users = self.get_all_users()
        total_users = len(users)
        
        broadcast['total'] = total_users
        
        logger.info(f"Начало рассылки {broadcast_id} для {total_users} пользователей")
        
        # Отправляем статус администратору
        try:
            bot.send_message(
                broadcast['admin_id'],
                f"📢 <b>Запущена массовая рассылка</b>\n\n"
                f"👥 Всего получателей: <b>{total_users}</b>\n"
                f"🕐 Время начала: {format_time()} • {format_date()}\n\n"
                f"<i>Статус будет обновляться по мере отправки...</i>"
            )
        except:
            pass
        
        sent_count = 0
        failed_count = 0
        failed_users = []
        
        # Отправляем каждому пользователю
        for i, user_id in enumerate(users, 1):
            broadcast['current'] = i
            
            try:
                # Проверяем, не отписался ли пользователь
                if self._can_send_to_user(user_id):
                    if message.get('text'):
                        bot.send_message(
                            user_id,
                            message['text'],
                            parse_mode="HTML",
                            disable_web_page_preview=not message.get('preview', True)
                        )
                    elif message.get('photo'):
                        bot.send_photo(
                            user_id,
                            message['photo'],
                            caption=message.get('caption', ''),
                            parse_mode="HTML"
                        )
                    elif message.get('document'):
                        bot.send_document(
                            user_id,
                            message['document'],
                            caption=message.get('caption', ''),
                            parse_mode="HTML"
                        )
                    
                    sent_count += 1
                    broadcast['sent'] = sent_count
                    
                    # Пауза между отправками чтобы не превысить лимиты API
                    if i % 20 == 0:
                        time.sleep(1)
                    if i % 100 == 0:
                        time.sleep(2)
                        
                else:
                    failed_count += 1
                    failed_users.append(user_id)
                    broadcast['failed'] = failed_count
                    
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 403:
                    # Пользователь заблокировал бота
                    logger.info(f"Пользователь {user_id} заблокировал бота")
                elif e.error_code == 400:
                    # Неверный запрос
                    logger.warning(f"Ошибка 400 для пользователя {user_id}")
                failed_count += 1
                failed_users.append(user_id)
                broadcast['failed'] = failed_count
                
                # Пауза при ошибках
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
                failed_count += 1
                failed_users.append(user_id)
                broadcast['failed'] = failed_count
                time.sleep(1)
            
            # Обновляем статус каждые 50 пользователей
            if i % 50 == 0:
                self._send_progress_update(broadcast_id)
        
        # Завершаем рассылку
        broadcast['status'] = 'completed'
        broadcast['sent'] = sent_count
        broadcast['failed'] = failed_count
        
        # Сохраняем статистику
        self.broadcast_stats[broadcast_id] = {
            'admin_id': broadcast['admin_id'],
            'message_type': 'text' if message.get('text') else 'media',
            'start_time': broadcast['start_time'],
            'end_time': datetime.now(),
            'total': total_users,
            'sent': sent_count,
            'failed': failed_count,
            'failed_users': failed_users[:100]  # Сохраняем только первые 100
        }
        
        # Отправляем итоговый отчет
        self._send_final_report(broadcast_id)
        
        logger.info(f"Рассылка {broadcast_id} завершена: {sent_count} успешно, {failed_count} неудачно")
    
    def _can_send_to_user(self, user_id):
        """Проверить, можно ли отправить сообщение пользователю"""
        try:
            # Пытаемся получить информацию о чате
            chat = bot.get_chat(user_id)
            return True
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 403:
                return False
            return True
        except:
            return True
    
    def _send_progress_update(self, broadcast_id):
        """Отправить обновление прогресса администратору"""
        broadcast = self.active_broadcasts.get(broadcast_id)
        if not broadcast:
            return
        
        try:
            progress = (broadcast['current'] / broadcast['total']) * 100
            elapsed = datetime.now() - broadcast['start_time']
            elapsed_str = str(elapsed).split('.')[0]
            
            # Отправляем обновление только если прошло больше 30 секунд с последнего
            last_update = broadcast.get('last_update')
            if last_update and (datetime.now() - last_update).seconds < 30:
                return
            
            update_msg = bot.send_message(
                broadcast['admin_id'],
                f"📊 <b>Прогресс рассылки</b>\n\n"
                f"📈 Отправлено: <b>{broadcast['current']}/{broadcast['total']}</b>\n"
                f"📊 Прогресс: <b>{progress:.1f}%</b>\n"
                f"✅ Успешно: <b>{broadcast['sent']}</b>\n"
                f"❌ Ошибки: <b>{broadcast['failed']}</b>\n"
                f"⏱️ Время: {elapsed_str}\n\n"
                f"<i>Рассылка выполняется...</i>"
            )
            
            # Удаляем предыдущее сообщение о прогрессе
            last_msg_id = broadcast.get('last_msg_id')
            if last_msg_id:
                try:
                    bot.delete_message(broadcast['admin_id'], last_msg_id)
                except:
                    pass
            
            broadcast['last_msg_id'] = update_msg.message_id
            broadcast['last_update'] = datetime.now()
            
        except Exception as e:
            logger.error(f"Progress update error: {e}")
    
    def _send_final_report(self, broadcast_id):
        """Отправить итоговый отчет"""
        stats = self.broadcast_stats.get(broadcast_id)
        if not stats:
            return
        
        try:
            duration = stats['end_time'] - stats['start_time']
            duration_str = str(duration).split('.')[0]
            
            success_rate = (stats['sent'] / stats['total']) * 100 if stats['total'] > 0 else 0
            
            report_text = f"""📊 <b>ИТОГ РАССЫЛКИ</b>

<code>━━━━━━━━━━━━━━</code>

<b>📊 Статистика:</b>
• 👥 Всего получателей: <b>{stats['total']}</b>
• ✅ Успешно отправлено: <b>{stats['sent']}</b>
• ❌ Не удалось отправить: <b>{stats['failed']}</b>
• 📈 Успешность: <b>{success_rate:.1f}%</b>

<b>⏱️ Время выполнения:</b>
• 🕐 Начало: {stats['start_time'].strftime('%H:%M:%S')}
• 🕐 Конец: {stats['end_time'].strftime('%H:%M:%S')}
• ⏱️ Длительность: {duration_str}

<b>📝 Тип сообщения:</b>
• 📄 {stats['message_type']}

<code>━━━━━━━━━━━━━━</code>"""
            
            if stats['failed'] > 0:
                failed_list = '\n'.join([f"• <code>{uid}</code>" for uid in stats['failed_users'][:10]])
                report_text += f"\n<b>❌ Пользователи с ошибками (первые 10):</b>\n{failed_list}"
                
                if len(stats['failed_users']) > 10:
                    report_text += f"\n\n<i>... и еще {len(stats['failed_users']) - 10} пользователей</i>"
            
            bot.send_message(stats['admin_id'], report_text)
            
        except Exception as e:
            logger.error(f"Final report error: {e}")

broadcast_manager = BroadcastManager()

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
    
    # Если админ - показываем расширенное меню
    if user_id in ADMIN_IDS:
        text = """<b>🛠️ Служба поддержки 👑 АДМИН</b>

Добро пожаловать в админ-панель!

<code>━━━━━━━━━━━━━━</code>

<b>📋 Доступные действия:</b>
• 📩 Создать обращение
• 📋 FAQ и частые вопросы
• 📊 Мои обращения
• 💭 Оставить отзыв
• ℹ️ Справка
• 👑 <b>Админ-панель</b>

<code>━━━━━━━━━━━━━━</code>

<i>Выберите действие:</i>"""
        
        buttons = [
            [("📩 Создать обращение", "create_ticket")],
            [("📋 FAQ", "show_faq"), ("📊 Мои обращения", "my_tickets")],
            [("💭 Отзыв", "feedback"), ("ℹ️ Справка", "help")],
            [("👑 Админ-панель", "admin_panel")]
        ]
    else:
        text, markup = MenuManager.main_menu()
        sent_msg = bot.send_message(user_id, text, reply_markup=markup)
        state.add_message(user_id, sent_msg.message_id, "main_menu")
        state.clear_state(user_id)
        return
    
    markup = create_inline_keyboard(buttons)
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

# ===== АДМИН КОМАНДЫ =====
@bot.message_handler(commands=['broadcast'], func=lambda m: m.from_user.id in ADMIN_IDS)
def broadcast_command(message):
    """Команда для начала рассылки"""
    user_id = message.from_user.id
    
    text = """<b>📢 МАССОВАЯ РАССЫЛКА</b>

<code>━━━━━━━━━━━━━━</code>

Отправьте сообщение для рассылки всем пользователям.

<b>Поддерживаемые форматы:</b>
• 📝 Текстовые сообщения
• 🖼️ Фотографии с подписью
• 📎 Документы с подписью

<code>━━━━━━━━━━━━━━</code>

<b>Внимание:</b>
• Рассылка может занять длительное время
• Не отправляйте команды во время рассылки
• Статус будет отправляться автоматически
• Пользователи, заблокировавшие бота, будут пропущены

<code>━━━━━━━━━━━━━━</code>

<i>Отправьте сообщение для рассылки или нажмите "Отмена":</i>"""
    
    markup = create_inline_keyboard([
        [("❌ Отмена", "admin_cancel")]
    ])
    
    sent_msg = bot.send_message(user_id, text, reply_markup=markup)
    state.add_message(user_id, sent_msg.message_id, "broadcast_menu")
    state.set_state(user_id, "waiting_broadcast")

@bot.message_handler(commands=['stats'], func=lambda m: m.from_user.id in ADMIN_IDS)
def stats_command(message):
    """Команда для получения статистики"""
    user_id = message.from_user.id
    
    markup = create_inline_keyboard([
        [("📊 Показать статистику", "admin_stats")],
        [("📢 Массовая рассылка", "admin_broadcast")]
    ])
    
    bot.send_message(
        user_id,
        "<b>📊 Панель администратора</b>\n\nВыберите действие:",
        reply_markup=markup
    )

@bot.message_handler(func=lambda m: m.from_user.id in ADMIN_IDS and state.get_state(m.from_user.id) == "waiting_broadcast")
def handle_broadcast_message(message):
    """Обработка сообщения для рассылки"""
    admin_id = message.from_user.id
    
    # Формируем сообщение для рассылки
    broadcast_message = {}
    
    if message.text:
        broadcast_message['text'] = message.text
        broadcast_message['preview'] = True
        preview_text = message.text[:100] + "..." if len(message.text) > 100 else message.text
        preview_type = "📝 Текст"
        
    elif message.photo:
        broadcast_message['photo'] = message.photo[-1].file_id
        broadcast_message['caption'] = message.caption or ""
        preview_text = message.caption[:100] + "..." if message.caption and len(message.caption) > 100 else (message.caption or "Фото")
        preview_type = "🖼️ Фото"
        
    elif message.document:
        broadcast_message['document'] = message.document.file_id
        broadcast_message['caption'] = message.caption or ""
        preview_text = message.caption[:100] + "..." if message.caption and len(message.caption) > 100 else (message.caption or message.document.file_name)
        preview_type = "📎 Документ"
        
    else:
        bot.send_message(admin_id, "❌ Поддерживаются только текстовые сообщения, фото и документы.")
        state.clear_state(admin_id)
        return
    
    # Получаем количество пользователей
    users = broadcast_manager.get_all_users()
    total_users = len(users)
    
    # Подтверждение
    confirm_text = f"""<b>📢 ПОДТВЕРЖДЕНИЕ РАССЫЛКИ</b>

<code>━━━━━━━━━━━━━━</code>

<b>📊 Статистика:</b>
• 👥 Получателей: <b>{total_users}</b>
• 📄 Тип: {preview_type}
• 👨‍💼 Администратор: @{message.from_user.username}

<b>📝 Содержание:</b>
{preview_text}

<code>━━━━━━━━━━━━━━</code>

<b>⚠️ ВНИМАНИЕ:</b>
Рассылка будет отправлена <b>ВСЕМ</b> пользователям бота.
Отменить после начала будет невозможно.

<code>━━━━━━━━━━━━━━</code>

<i>Вы уверены, что хотите начать рассылку?</i>"""
    
    markup = create_inline_keyboard([
        [("✅ НАЧАТЬ РАССЫЛКУ", f"admin_broadcast_confirm")],
        [("❌ ОТМЕНА", "admin_cancel")]
    ])
    
    # Сохраняем сообщение для рассылки
    state.set_state(admin_id, "confirming_broadcast", {
        'broadcast_message': broadcast_message,
        'total_users': total_users,
        'preview_type': preview_type,
        'preview_text': preview_text
    })
    
    sent_msg = bot.send_message(admin_id, confirm_text, reply_markup=markup)
    state.add_message(admin_id, sent_msg.message_id, "broadcast_confirm")

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
            
        elif call.data == "admin_panel":
            if call.from_user.id not in ADMIN_IDS:
                bot.answer_callback_query(call.id, "⛔ Доступ запрещен")
                return
            
            text = """<b>👑 АДМИН - ПАНЕЛЬ УПРАВЛЕНИЯ</b>

<code>━━━━━━━━━━━━━━</code>

<b>📊 Статистика:</b>
• /stats - общая статистика
• /broadcast - массовая рассылка

<b>⚙️ Управление:</b>
• Мониторинг обращений
• Управление пользователями
• Настройки системы

<code>━━━━━━━━━━━━━━</code>

<i>Выберите действие:</i>"""
            
            markup = create_inline_keyboard([
                [("📊 Статистика", "admin_stats"), ("📢 Рассылка", "admin_broadcast")],
                [("👤 Пользователи", "admin_users"), ("⚙️ Настройки", "admin_settings")],
                [("🔙 Назад", "main_menu")]
            ])
            
            bot.edit_message_text(
                text,
                chat_id=call.message.chat.id,
                message_id=message_id,
                reply_markup=markup
            )
            state.add_message(call.from_user.id, message_id, "admin_panel")
            
        elif call.data.startswith("admin_"):
            handle_admin_callback(call)
            return
            
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

def handle_admin_callback(call):
    user_id = call.from_user.id
    
    if user_id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "⛔ Доступ запрещен")
        return
    
    if call.data == "admin_cancel":
        # Отмена рассылки
        state.clear_state(user_id)
        text, markup = MenuManager.main_menu()
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup
        )
        state.add_message(user_id, call.message.message_id, "main_menu")
        bot.answer_callback_query(call.id, "❌ Отменено")
        
    elif call.data == "admin_broadcast_confirm":
        # Подтверждение рассылки
        broadcast_data = state.get_data(user_id)
        
        if not broadcast_data or 'broadcast_message' not in broadcast_data:
            bot.answer_callback_query(call.id, "❌ Данные рассылки не найдены")
            return
        
        # Начинаем рассылку
        bot.edit_message_text(
            "🔄 <b>Запуск рассылки...</b>\n\n<i>Пожалуйста, подождите. Статус будет отправлен отдельным сообщением.</i>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        
        # Запускаем рассылку
        broadcast_id = broadcast_manager.send_broadcast(
            user_id,
            broadcast_data['broadcast_message']
        )
        
        # Очищаем состояние
        state.clear_state(user_id)
        
        bot.answer_callback_query(call.id, "✅ Рассылка запущена")
        
    elif call.data == "admin_stats":
        # Статистика бота
        conn = sqlite3.connect('support.db')
        c = conn.cursor()
        
        try:
            # Общая статистика
            c.execute("SELECT COUNT(*) FROM users")
            total_users = c.fetchone()[0]
            
            c.execute("SELECT COUNT(*) FROM tickets")
            total_tickets = c.fetchone()[0]
            
            c.execute("SELECT COUNT(*) FROM tickets WHERE status = 'open'")
            open_tickets = c.fetchone()[0]
            
            c.execute("SELECT COUNT(*) FROM tickets WHERE status = 'in_progress'")
            progress_tickets = c.fetchone()[0]
            
            c.execute("SELECT COUNT(*) FROM tickets WHERE created_at > datetime('now', '-1 day')")
            today_tickets = c.fetchone()[0]
            
            stats_text = f"""<b>📊 СТАТИСТИКА БОТА</b>

<code>━━━━━━━━━━━━━━</code>

<b>👥 Пользователи:</b> <code>{total_users}</code>
<b>📩 Всего обращений:</b> <code>{total_tickets}</code>

<b>📊 Статус обращений:</b>
• 🟡 Открыто: <code>{open_tickets}</code>
• 🟠 В работе: <code>{progress_tickets}</code>
• 📈 Сегодня: <code>{today_tickets}</code>

<code>━━━━━━━━━━━━━━</code>

<b>Последние рассылки:</b>"""
            
            # Статистика рассылок
            broadcast_stats = list(broadcast_manager.broadcast_stats.items())[-5:]
            for bid, stats in broadcast_stats:
                time_str = stats['start_time'].strftime("%d.%m %H:%M")
                success_rate = (stats['sent'] / stats['total']) * 100 if stats['total'] > 0 else 0
                stats_text += f"\n• {time_str}: {stats['sent']}/{stats['total']} ({success_rate:.0f}%)"
            
            bot.send_message(user_id, stats_text)
            bot.answer_callback_query(call.id, "📊 Статистика отправлена")
            
        except Exception as e:
            logger.error(f"Stats error: {e}")
            bot.answer_callback_query(call.id, "❌ Ошибка получения статистики")
        finally:
            conn.close()
    
    elif call.data == "admin_broadcast":
        # Вызываем команду рассылки
        broadcast_command(types.Message(
            message_id=call.message.message_id,
            from_user=call.from_user,
            chat=call.message.chat,
            date=call.message.date,
            content_type='text',
            json_string='{}'
        ))
        
    elif call.data == "admin_users" or call.data == "admin_settings":
        # Заглушки для будущих функций
        bot.answer_callback_query(call.id, "⚙️ Функция в разработке")

# ===== ОБРАБОТКА СООБЩЕНИЙ =====
@bot.message_handler(func=lambda message: message.chat.type == 'private' and not message.text.startswith('/'))
def private_message_handler(message):
    user_id = message.from_user.id
    user_state = state.get_state(user_id)
    
    if user_state == "waiting_feedback":
        handle_feedback(message)
    elif user_state == "creating_ticket" or not user_state:
        handle_ticket_creation(message)

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
    print(f"Админы: {len(ADMIN_IDS)} пользователей")
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
    print("• 📢 УЛУЧШЕННАЯ массовая рассылка")
    print("• 👑 Админ-панель с статистикой")
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