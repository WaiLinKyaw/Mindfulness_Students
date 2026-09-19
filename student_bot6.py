import os
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import asyncpg
from telegram.request import HTTPXRequest
from telegram.error import NetworkError, TimedOut
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

load_dotenv()

STUDENT_BOT_TOKEN = os.getenv("STUDENT_BOT_TOKEN")
TEACHER_BOT_TOKEN = os.getenv("TEACHER_BOT_TOKEN")
TEACHER_CHAT_ID = os.getenv("TEACHER_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# Supabase Credentials
DB_USER = "postgres.vqcoaukndkspyddpnvhd"
DB_PASSWORD = "F2%e.b6ed4/96y!"
DB_HOST = "aws-0-ap-northeast-1.pooler.supabase.com"
DB_PORT = 5432
DB_NAME = "postgres"


MM_TZ = timezone(timedelta(hours=6, minutes=30))
db_pool = None

# ================= Database Helpers =================

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        min_size=1,
        max_size=5,
    )
    print("✅ Connected to Supabase Pooler.")

async def close_db():
    global db_pool
    if db_pool:
        await db_pool.close()

# ----------------- Bot Commands & Handlers -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO students (student_id, student_name)
            VALUES ($1, $2)
            ON CONFLICT (student_id) DO UPDATE SET student_name = $2;
            """,
            user.id, user.first_name
        )

    text = (
        f"မင်္ဂလာပါ {user.first_name} 🙏\n\n"
        "ဤ Bot သည် နေ့စဉ် သတိပဋ္ဌာန်နှင့် စိတ်လေ့ကျင့်မှု (Mindfulness Routine) များကို မှတ်သားပေးမည့် Bot ဖြစ်ပါသည်။\n\n"
        "• မနက်ပိုင်း (5:00 AM - 12:00 PM)\n"
        "• နေ့လည်ပိုင်း (12:00 PM - 5:00 PM)\n"
        "• ညနေပိုင်း (5:00 PM - 12:00 AM)\n\n"
        "သတ်မှတ်ချိန်များတွင် အလုပ်စာရင်းများ ပို့ပေးပါမည်။ ပြီးစီးပါက Done ခလုတ်ကို နှိပ်ပေးပါခင်ဗျာ။\n"
        "သိလိုရာမေးခွန်း သို့မဟုတ် အတွေ့အကြုံများကိုလည်း အချိန်မရွေး စာရိုက်ပို့နိုင်ပါသည်။"
    )
    await update.message.reply_text(text)
    
async def check_and_send_missed_routines(app):
    """Bot စတက်ချိန်တွင် လက်ရှိအချိန်အရ လွတ်သွားသော Routine များ ရှိပါက ချက်ချင်း ပို့ပေးခြင်း"""
    now = datetime.now(MM_TZ)
    current_hour = now.hour
    today = now.date()

    # လက်ရှိအချိန်အရ ဘယ်အချိန်ပိုင်း ရောက်နေပြီလဲ သတ်မှတ်ခြင်း
    active_sections = []
    if current_hour >= 5 and current_hour < 12:
        active_sections.append(("morning", "🌅 *(က) မနက်ပိုင်း လေ့ကျင့်မှု (5:00 AM - 12:00 PM)*"))
    elif current_hour >= 12 and current_hour < 17:
        active_sections.append(("afternoon", "☀️ *(ခ) နေ့လည်ပိုင်း လေ့ကျင့်မှု (12:00 PM - 5:00 PM)*"))
    elif current_hour >= 17:
        active_sections.append(("evening", "🌙 *(ဂ) ညနေပိုင်း လေ့ကျင့်မှု (5:00 PM - 12:00 AM)*"))

    if not active_sections:
        return

    async with db_pool.acquire() as conn:
        students = await conn.fetch("SELECT student_id FROM students;")

    if not students:
        return

    for section_name, header_text in active_sections:
        # ယနေ့အတွက် ဤ section ရှိ task တစ်ခုခု စာရင်းဝင်ပြီးသားလား စစ်ဆေးခြင်း
        # (တပည့်တစ်ဦးဦးဆီက log တစ်ခုခု ရှိနေပါက ပို့ပြီးပြီဟု သတ်မှတ်၍ ထပ်မပို့ပါ)
        query = """
            SELECT COUNT(*) 
            FROM student_daily_logs log
            JOIN routine_templates rt ON log.template_id = rt.id
            WHERE rt.section = $1 AND log.log_date = $2;
        """
        async with db_pool.acquire() as conn:
            sent_count = await conn.fetchval(query, section_name, today)

        if sent_count == 0:
            print(f"⚠️ [Startup Check] {section_name.capitalize()} Routine လွတ်နေသည်ကို တွေ့ရှိရသဖြင့် ယခု ပို့ဆောင်ပေးနေပါသည်...")
            await broadcast_routine(app, section_name, header_text)
            print(f"✅ [Startup Check] {section_name.capitalize()} Routine အောင်မြင်စွာ ပို့ဆောင်ပြီးပါပြီ။")
        else:
            print(f"ℹ️ [Startup Check] {section_name.capitalize()} Routine သည် ယနေ့အတွက် ပို့ဆောင်ပြီးသား ဖြစ်ပါသည်။")

async def handle_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "already_done":
        await query.answer("ဤအလုပ်ကို ပြီးစီးကြောင်း မှတ်သားပြီးဖြစ်ပါသည်ခင်ဗျာ။", show_alert=False)
        return

    if not data.startswith("done:"):
        return

    template_id = int(data.split(":")[1])
    user_id = query.from_user.id
    user_name = query.from_user.first_name
    today = datetime.now(MM_TZ).date()

    # Database တွင် Update သို့မဟုတ် Insert လုပ်ခြင်း
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO student_daily_logs (student_id, student_name, template_id, log_date, is_done, completed_at)
            VALUES ($1, $2, $3, $4, TRUE, NOW())
            ON CONFLICT (student_id, template_id, log_date)
            DO UPDATE SET is_done = TRUE, completed_at = NOW();
            """,
            user_id, user_name, template_id, today
        )

    # နှိပ်လိုက်သော Task Message လေးပေါ်တွင်သာ ခလုတ်ကို '✅ ပြီးပါပြီ' သို့ ပြောင်းပေးခြင်း
    updated_keyboard = [
        [InlineKeyboardButton("✅ ပြီးပါပြီ", callback_data="already_done")]
    ]
    try:
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(updated_keyboard)
        )
    except Exception as e:
        print(f"Callback edit error: {e}")

async def handle_student_qa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """တပည့်များ ရိုက်ပို့သော စာကို မှတ်သားပြီး ဆရာ့ထံ Inline Actions ခလုတ်များဖြင့် Forward ပို့ခြင်း"""
    user = update.effective_user
    text = update.message.text
    today = datetime.now(MM_TZ).date()

    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO student_reflections (student_id, student_name, log_date, reflection_text)
            VALUES ($1, $2, $3, $4);
            """,
            user.id, user.first_name, today, text
        )

    await update.message.reply_text("✅ ဆရာ့ထံ အကြောင်းကြားစာ ပို့လိုက်ပါပြီခင်ဗျာ။")

    # Teacher Bot ဆီသို့ ခလုတ်များ တွဲလျက် Alert ပို့ခြင်း
    if TEACHER_BOT_TOKEN and TEACHER_CHAT_ID:
        try:
            from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
            
            # ဆရာက တစ်ချက်နှိပ်ရုံဖြင့် Checklist ပြန်ပို့ပေးနိုင်သော ခလုတ်များ
            keyboard = [
                [
                    InlineKeyboardButton("🌅 မနက်ပိုင်း ပို့မည်", callback_data=f"send_routine:morning:{user.id}"),
                    InlineKeyboardButton("☀️ နေ့လည်ပိုင်း ပို့မည်", callback_data=f"send_routine:afternoon:{user.id}")
                ],
                [
                    InlineKeyboardButton("🌙 ညနေပိုင်း ပို့မည်", callback_data=f"send_routine:evening:{user.id}")
                ]
            ]
            
            alert_msg = (
                f"📩 *တပည့်ထံမှ စာ/တောင်းဆိုချက် ရောက်ရှိပါသည်*\n\n"
                f"👤 တပည့်: *{user.first_name}* (ID: `{user.id}`)\n"
                f"📝 စာသား:\n\"{text}\"\n\n"
                f"👉 အောက်ပါခလုတ်ကို နှိပ်၍ အဆိုပါတပည့်ထံ Checklist ချက်ချင်း ပို့နိုင်ပါသည် (သို့မဟုတ် `/reply {user.id} <အဖြေစာ>` ဖြင့် စာပြန်နိုင်ပါသည်)။"
            )

            async with Bot(token=TEACHER_BOT_TOKEN) as teacher_bot:
                await teacher_bot.send_message(
                    chat_id=TEACHER_CHAT_ID,
                    text=alert_msg,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
        except Exception as e:
            print(f"Failed to notify teacher: {e}")


# ----------------- Automated Routines & Reminders -----------------

async def broadcast_routine(app, section_name: str, header_text: str, target_chat_id=None):
    """Routine Tasks များကို တစ်ခုချင်းစီ သီးခြား Card အနေဖြင့် Done Button နှင့် တွဲပို့ပေးခြင်း"""
    today = datetime.now(MM_TZ).date()
    async with db_pool.acquire() as conn:
        if target_chat_id:
            students = [{"student_id": target_chat_id}]
        else:
            students = await conn.fetch("SELECT student_id FROM students;")

        tasks = await conn.fetch(
            #"SELECT id, title, description FROM routine_templates WHERE section = $1 ORDER BY task_order ASC;",
            #"SELECT id, title, description FROM routine_templates WHERE section = $1 AND is_active = TRUE ORDER BY task_order ASC;",
            "SELECT id, title, description FROM routine_templates WHERE section = $1 AND status = 'active' ORDER BY task_order ASC;",
            section_name
        )

    if not tasks or not students:
        return

    for stu in students:
        chat_id = stu["student_id"]

        # ယခင် ပြီးစီးထားပြီးသော Tasks များကို စစ်ဆေးခြင်း
        async with db_pool.acquire() as conn:
            done_rows = await conn.fetch(
                "SELECT template_id FROM student_daily_logs WHERE student_id = $1 AND log_date = $2 AND is_done = TRUE;",
                chat_id, today
            )
        done_ids = {r["template_id"] for r in done_rows}

        try:
            # ၁။ အစပိုင်းတွင် Header (ခေါင်းစဉ်) သီးသန့် ပို့ပေးခြင်း
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"{header_text}\n\nအောက်ပါ လေ့ကျင့်မှုများကို ပြုလုပ်ပြီးပါက သက်ဆိုင်ရာခလုတ်ကို နှိပ်ပေးပါခင်ဗျာ -",
                parse_mode="Markdown"
            )

            # ၂။ Task တစ်ခုချင်းစီကို စာသား + ခလုတ် တွဲလျက် သီးခြားစီ ပို့ပေးခြင်း
            for idx, t in enumerate(tasks, start=1):
                t_id = t["id"]
                task_text = f"📌 *{idx}။ {t['title']}*"
                if t["description"]:
                    task_text += f"\n_{t['description']}_"

                # ပြီးစီးပြီးသား ဟုတ်/မဟုတ် စစ်ဆေးပြီး ခလုတ်သတ်မှတ်ခြင်း
                if t_id in done_ids:
                    keyboard = [[InlineKeyboardButton("✅ ပြီးပါပြီ", callback_data="already_done")]]
                else:
                    keyboard = [[InlineKeyboardButton("လုပ်ဆောင်ပြီး (Done)", callback_data=f"done:{t_id}")]]

                await app.bot.send_message(
                    chat_id=chat_id,
                    text=task_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )

        except Exception as e:
            print(f"Error sending individual routines to {chat_id}: {e}")



async def handle_dismiss_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # callback_data မှ batch_id ရယူခြင်း ("dismiss_batch:abc12345")
    batch_id = query.data.split(":")[1]
    chat_id = query.message.chat_id

    # Database မှ အဆိုပါ batch ထဲက message_ids များကို ယူခြင်း
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT message_ids FROM routine_batch_logs WHERE batch_id = $1 AND student_id = $2;",
            batch_id, chat_id
        )

        if not row or not row["message_ids"]:
            await query.edit_message_text("စာရင်းဟောင်းများကို ရှာမတွေ့တော့ပါခင်ဗျာ။")
            return

        message_ids_to_delete = row["message_ids"]

        # Telegram Message များကို တစ်ခုချင်းစီ လိုက်ဖျက်ပေးခြင်း
        for msg_id in message_ids_to_delete:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except TelegramError:
                pass

        # DB မှ စာရင်းကိုပါ ရှင်းပစ်ခြင်း
        await conn.execute("DELETE FROM routine_batch_logs WHERE batch_id = $1;", batch_id)

    # ကျောင်းသားထံ ဖျက်ပြီးကြောင်း ခေတ္တ အသိပေးစာ ပို့ခြင်း
    await context.bot.send_message(
        chat_id=chat_id,
        text="🗑️ ထပ်နေသော စာရင်းကို အောင်မြင်စွာ ပယ်ဖျက်ပြီးပါပြီခင်ဗျာ။"
    )
            
            
async def check_incomplete_tasks(application, section_name: str, section_label: str):
    """သတ်မှတ်ထားသော session တွင် task ပြီးမြောက်ခြင်း မရှိသေးသူများကို သတိပေးစာတို ပို့ခြင်း"""
    today = datetime.now(MM_TZ).date()

    async with db_pool.acquire() as conn:
        # အဆိုပါ section တွင် active task များထဲမှ done မနှိပ်ရသေးသော task ကျန်နေသည့် ကျောင်းသားများကို ရှာခြင်း
        incomplete_students = await conn.fetch(
            """
            SELECT s.student_id 
            FROM students s
            WHERE EXISTS (
                SELECT 1 FROM routine_templates t
                WHERE t.section = $1 AND t.status = 'active'
                AND NOT EXISTS (
                    SELECT 1 FROM daily_routine_logs l
                    WHERE l.student_id = s.student_id
                      AND l.task_id = t.id
                      AND l.log_date = $2
                      AND l.is_completed = TRUE
                )
            );
            """,
            section_name, today
        )

    # သတိပေးစာ ပို့ဆောင်ခြင်း
    for stu in incomplete_students:
        chat_id = stu["student_id"]
        try:
            await application.bot.send_message(
                chat_id=chat_id,
                text=f"🔔 *သတိပေးချက်*\n\nယနေ့ {section_label} လေ့ကျင့်မှုများ မပြီးပြတ်သေးပါက အချိန်မီ အပြီးသတ် အမှတ်ခြစ်ပေးပါရန် အသိပေးအပ်ပါသည်ခင်ဗျာ။",
                parse_mode="Markdown"
            )
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Error sending reminder to {chat_id}: {e}")

# ----------------- Main Runner -----------------

async def post_init(application):
    """Bot မစတင်မီ Database ချိတ်ဆက်ခြင်းနှင့် Scheduler စတင်ခြင်း"""
    # 1. Database Pool ချိတ်ဆက်ခြင်း
    await init_db()

    # 2. Active Event Loop ပေါ်တွင် Scheduler ကို စတင်ခြင်း
    scheduler = AsyncIOScheduler(timezone="Asia/Yangon")

    # (မှတ်ချက်: Routine ပို့ခြင်းများကို teacher_bot မှ စီမံနေပြီဖြစ်ပါက student_bot ဘက်မှ broadcast_routine များကို ပိတ်ထားသင့်ပါသည်)
    # Reminder ပို့သည့် function တစ်ခုတည်းသာ ချန်ထားနိုင်ပါသည်:
    #scheduler.add_job(
        #check_incomplete_morning_tasks,
        #CronTrigger(hour=12, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        #args=[application]
    #)
    # (၁) မနက်ပိုင်း မပြီးသေးသူများကို Reminder ပို့ခြင်း (နေ့လယ် 12:00 PM)
    scheduler.add_job(
        check_incomplete_tasks,
        CronTrigger(hour=12, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=[application, "morning", "မနက်ပိုင်း"],
        id="remind_morning"
    )

    # (၂) နေ့လယ်ပိုင်း မပြီးသေးသူများကို Reminder ပို့ခြင်း (ညနေ 5:00 PM)
    scheduler.add_job(
        check_incomplete_tasks,
        CronTrigger(hour=17, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=[application, "afternoon", "နေ့လယ်ပိုင်း"],
        id="remind_afternoon"
    )

    # (၃) ညနေပိုင်း မပြီးသေးသူများကို Reminder ပို့ခြင်း (ည 10:00 PM)
    scheduler.add_job(
        check_incomplete_tasks,
        CronTrigger(hour=22, minute=0, timezone=ZoneInfo("Asia/Yangon")),
        args=[application, "evening", "ညနေပိုင်း"],
        id="remind_evening"
    )

    scheduler.start()
    print("⏰ Student Reminder Scheduler started...")
   
    


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Network error များကြောင့် Bot ရပ်မသွားစေရန် ထိန်းကျောင်းခြင်း"""
    err = context.error
    if isinstance(err, (NetworkError, TimedOut)):
        print(f"⚠️ Network glitch ခေတ္တဖြစ်ပေါ်ပါသည် (အလိုအလျောက် ပြန်ချိတ်ပါမည်): {err}")
    else:
        print(f"❌ Error ဖြစ်ပေါ်ပါသည်: {err}")


def main():
   
    # Network timeout ကြောင့် ReadError မဖြစ်စေရန် timeout များ တိုးပေးခြင်း
    request_config = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=30.0,      # default 5.0 မှ 30.0 သို့ မြှင့်ခြင်း
        write_timeout=30.0,
        connect_timeout=30.0,
        pool_timeout=30.0
    )
    # ApplicationBuilder တွင် post_init ချိတ်ဆက်ခြင်း
    app = (
        ApplicationBuilder()
        .token(STUDENT_BOT_TOKEN)
        .request(request_config)
        .post_init(post_init)
        .build()
    )

    # Handlers များ ထည့်သွင်းခြင်း
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(handle_dismiss_batch, pattern=r"^dismiss_batch:"))
    app.add_handler(CallbackQueryHandler(handle_done_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_student_qa))
    app.add_error_handler(error_handler)

    print("🚀 Student Bot is starting polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()