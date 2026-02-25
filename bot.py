"""
bot.py — Admin-only Telegram bot commands.

All commands are protected by the @admin_only decorator.
No public/subscriber interface — this bot is for personal use.

Commands:
  /start          Welcome & command list
  /status         Stats + current settings
  /queue          Show pending/approved posts with action buttons
  /preview <id>   Full text of a queued post
  /approve <id>   Approve a pending post
  /reject <id>    Reject a post
  /editpost <id> <text>  Replace the Arabic text of a post
  /addpost <text> Manually add a post (AI translates it)
  /postnow [id]   Force-post immediately (next in queue, or specific ID)
  /skippost       Move the first approved post to the back of the queue
  /clearqueue     Delete all rejected posts (frees space)
  /pause          Stop auto-posting
  /resume         Resume auto-posting
  /setdelay <n>   Change posting interval to N minutes (live, no restart)
  /approval on|off Toggle require-approval mode
  /summary        Generate and offer to post today's daily summary
  /help           Command reference
"""

import functools
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    filters,
    MessageHandler,
)

import config
from database import (
    clear_rejected,
    get_post,
    get_queue,
    get_setting,
    get_stats,
    move_to_back,
    set_setting,
    update_status,
    update_text,
    enqueue,
)
from ai import translate_and_rewrite, daily_summary
from poster import broadcast, post_next_in_queue, post_specific, reset_delay

logger = logging.getLogger(__name__)

ADMIN = config.ADMIN_ID


# ─────────────────────────────────────────────────────────────────────────────
#  AUTH DECORATOR
# ─────────────────────────────────────────────────────────────────────────────

def admin_only(func):
    """Silently ignore any message not from the configured admin user."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN:
            return   # say nothing to strangers
        return await func(update, context)
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _queue_buttons(post_id: int, show_approve: bool) -> InlineKeyboardMarkup:
    """Build inline keyboard for a queued post card."""
    top_row = []
    if show_approve:
        top_row.append(InlineKeyboardButton("✅ موافقة",   callback_data=f"approve_{post_id}"))
        top_row.append(InlineKeyboardButton("❌ رفض",      callback_data=f"reject_{post_id}"))
    else:
        top_row.append(InlineKeyboardButton("📤 نشر الآن", callback_data=f"postnow_{post_id}"))
        top_row.append(InlineKeyboardButton("❌ رفض",      callback_data=f"reject_{post_id}"))

    bottom_row = [
        InlineKeyboardButton("👁 معاينة كاملة", callback_data=f"preview_{post_id}"),
    ]
    return InlineKeyboardMarkup([top_row, bottom_row])


async def _reply(update: Update, text: str, **kwargs) -> None:
    await update.message.reply_text(text, parse_mode="HTML", **kwargs)


async def _alert(update: Update, text: str) -> None:
    """Short reply, no parse mode needed."""
    await update.message.reply_text(text)


# ─────────────────────────────────────────────────────────────────────────────
#  COMMANDS
# ─────────────────────────────────────────────────────────────────────────────

@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(
        update,
        "🤖 <b>بوت هدايا تيليغرام</b> — جاهز للعمل!\n\n"
        "أرسل /help لقائمة جميع الأوامر.",
    )


@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(
        update,
        "📚 <b>الأوامر المتاحة:</b>\n\n"
        "<b>قائمة المنشورات:</b>\n"
        "/status — إحصائيات وحالة البوت\n"
        "/queue — عرض المنشورات في الطابور\n"
        "/preview &lt;id&gt; — معاينة نص منشور كامل\n\n"
        "<b>إدارة المنشورات:</b>\n"
        "/approve &lt;id&gt; — الموافقة على منشور\n"
        "/reject &lt;id&gt; — رفض منشور\n"
        "/editpost &lt;id&gt; &lt;نص جديد&gt; — تعديل نص منشور\n"
        "/addpost &lt;نص&gt; — إضافة منشور يدوياً (الذكاء الاصطناعي يترجمه)\n\n"
        "<b>النشر:</b>\n"
        "/postnow — نشر فوري للمنشور التالي\n"
        "/postnow &lt;id&gt; — نشر منشور محدد فوراً\n"
        "/skippost — تخطي المنشور التالي (إرساله للنهاية)\n"
        "/summary — إنشاء ملخص يومي ونشره\n\n"
        "<b>الإعدادات:</b>\n"
        "/pause — إيقاف النشر التلقائي\n"
        "/resume — استئناف النشر التلقائي\n"
        "/setdelay &lt;دقائق&gt; — تغيير الفترة بين المنشورات\n"
        "/approval on|off — تشغيل/إيقاف وضع الموافقة\n"
        "/clearqueue — حذف جميع المنشورات المرفوضة",
    )


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats    = await get_stats()
    auto     = await get_setting("auto_post", "true")
    approval = await get_setting("require_approval", "false")
    delay    = await get_setting("post_delay_minutes", "30")
    sources  = config.source_channels()
    targets  = config.target_channels()

    await _reply(
        update,
        f"📊 <b>حالة البوت</b>\n\n"
        f"<b>اليوم:</b>\n"
        f"  📤 منشورات نُشرت: <code>{stats['today_posts']}</code>\n"
        f"  👀 رسائل رُصدت: <code>{stats['today_seen']}</code>\n"
        f"  🤖 رسائل عالجها AI: <code>{stats['today_proc']}</code>\n\n"
        f"<b>الطابور:</b>\n"
        f"  ⏳ بانتظار الموافقة: <code>{stats['pending']}</code>\n"
        f"  ✅ معتمدة جاهزة: <code>{stats['approved']}</code>\n"
        f"  ❌ مرفوضة: <code>{stats['rejected']}</code>\n"
        f"  ⚠️ فشلت: <code>{stats['failed']}</code>\n\n"
        f"<b>الإجمالي الكلي:</b>\n"
        f"  📨 منشورات نُشرت: <code>{stats['total_posted']}</code>\n"
        f"  🗄 رسائل مخزّنة: <code>{stats['seen_messages']}</code>\n\n"
        f"<b>الإعدادات:</b>\n"
        f"  النشر التلقائي: {'✅ مفعّل' if auto == 'true' else '❌ موقوف'}\n"
        f"  وضع الموافقة: {'✅ مفعّل' if approval == 'true' else '❌ موقوف'}\n"
        f"  الفترة بين المنشورات: <code>{delay} دقيقة</code>\n\n"
        f"<b>القنوات:</b>\n"
        f"  📡 مصادر: {len(sources)}\n"
        f"  📢 هدف: {', '.join(targets) or 'غير محدد'}",
    )


@admin_only
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    approval_mode = (await get_setting("require_approval", "false")) == "true"
    posts = await get_queue(limit=5)

    if not posts:
        await _alert(update, "📭 الطابور فارغ حالياً.")
        return

    await _alert(update, f"📋 <b>الطابور</b> ({len(posts)} منشور):" if False else
                 f"📋 الطابور ({len(posts)} منشور):")

    for post in posts:
        pid     = post["id"]
        status  = post["status"]
        preview = post["arabic_text"][:200] + ("…" if len(post["arabic_text"]) > 200 else "")
        media   = f" | 🖼 {post['media_type']}" if post.get("media_type") else ""
        show_approve = approval_mode and status == "pending"

        await update.message.reply_text(
            f"<code>#{pid}</code>  [{status}]{media}\n\n{preview}",
            parse_mode="HTML",
            reply_markup=_queue_buttons(pid, show_approve),
        )


@admin_only
async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await _alert(update, "❓ الاستخدام: /preview <رقم>")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await _alert(update, "❌ رقم غير صحيح.")
        return

    post = await get_post(pid)
    if not post:
        await _alert(update, f"❌ لا يوجد منشور برقم {pid}.")
        return

    media_info = f"\n🖼 <b>نوع الميديا:</b> {post['media_type']}" if post.get("media_type") else ""
    src_info   = f"\n📡 <b>المصدر:</b> <code>{post['source_channel']}</code>" if post.get("source_channel") else ""

    await _reply(
        update,
        f"👁 <b>معاينة المنشور #{pid}</b>  [{post['status']}]"
        f"{src_info}{media_info}\n\n"
        f"{post['arabic_text']}",
    )


@admin_only
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await _alert(update, "❓ الاستخدام: /approve <رقم>")
        return
    try:
        pid = int(context.args[0])
        await update_status(pid, "approved")
        await _alert(update, f"✅ تم اعتماد المنشور #{pid}.")
    except ValueError:
        await _alert(update, "❌ رقم غير صحيح.")


@admin_only
async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await _alert(update, "❓ الاستخدام: /reject <رقم>")
        return
    try:
        pid = int(context.args[0])
        await update_status(pid, "rejected")
        await _alert(update, f"🗑️ تم رفض المنشور #{pid}.")
    except ValueError:
        await _alert(update, "❌ رقم غير صحيح.")


@admin_only
async def cmd_editpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /editpost <id> <new arabic text>
    Replaces the stored Arabic text of a queued post.
    """
    if not context.args or len(context.args) < 2:
        await _alert(update, "❓ الاستخدام: /editpost <رقم> <النص الجديد>")
        return
    try:
        pid      = int(context.args[0])
        new_text = " ".join(context.args[1:])
    except ValueError:
        await _alert(update, "❌ رقم غير صحيح.")
        return

    post = await get_post(pid)
    if not post:
        await _alert(update, f"❌ لا يوجد منشور برقم {pid}.")
        return

    await update_text(pid, new_text)
    preview = new_text[:200] + ("…" if len(new_text) > 200 else "")
    await _reply(update, f"✏️ تم تعديل المنشور #{pid}.\n\n<b>المعاينة:</b>\n{preview}")


@admin_only
async def cmd_addpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addpost <text>
    AI translates the text and adds it to the queue.
    """
    if not context.args:
        await _alert(update, "❓ الاستخدام: /addpost <النص أو الرابط أو أي محتوى>")
        return

    original = " ".join(context.args)
    await _alert(update, "⚙️ جاري المعالجة بالذكاء الاصطناعي…")

    arabic = await translate_and_rewrite(original)
    if not arabic:
        await _alert(update, "❌ فشلت المعالجة. تحقق من مفتاح Groq API.")
        return

    require_approval = (await get_setting("require_approval", "false")) == "true"
    status  = "pending" if require_approval else "approved"
    pid     = await enqueue(arabic_text=arabic, original_text=original, status=status)
    preview = arabic[:300] + ("…" if len(arabic) > 300 else "")

    await _reply(
        update,
        f"✅ أُضيف إلى الطابور (رقم: <code>{pid}</code>  حالة: {status})\n\n"
        f"<b>المعاينة:</b>\n{preview}",
    )


@admin_only
async def cmd_postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /postnow        → force-post the next approved item
    /postnow <id>   → approve + force-post a specific item
    """
    bot = context.bot

    if context.args:
        try:
            pid = int(context.args[0])
        except ValueError:
            await _alert(update, "❌ رقم غير صحيح.")
            return
        await _alert(update, f"⚡ جاري نشر المنشور #{pid}…")
        ok = await post_specific(bot, pid)
        await _alert(update, f"✅ تم النشر!" if ok else f"❌ فشل النشر للمنشور #{pid}.")
    else:
        await _alert(update, "⚡ جاري نشر المنشور التالي…")
        reset_delay()
        ok = await post_next_in_queue(bot, skip_delay=True)
        await _alert(update, "✅ تم النشر!" if ok else "📭 لا يوجد محتوى جاهز في الطابور.")


@admin_only
async def cmd_skippost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Move the oldest approved post to the back of the queue."""
    from database import get_next_approved
    post = await get_next_approved()
    if not post:
        await _alert(update, "📭 لا يوجد منشور معتمد في الطابور.")
        return
    await move_to_back(post["id"])
    await _alert(update, f"⏭️ تم تأجيل المنشور #{post['id']} إلى آخر الطابور.")


@admin_only
async def cmd_clearqueue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete all rejected posts to clean up the database."""
    count = await clear_rejected()
    await _alert(update, f"🗑️ تم حذف {count} منشور مرفوض من قاعدة البيانات.")


@admin_only
async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_setting("auto_post", "false")
    await _alert(update, "⏸️ تم إيقاف النشر التلقائي.")


@admin_only
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_setting("auto_post", "true")
    await _alert(update, "▶️ تم استئناف النشر التلقائي.")


@admin_only
async def cmd_setdelay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setdelay <minutes>
    Change the auto-post interval on the fly without restarting.
    """
    if not context.args:
        current = await get_setting("post_delay_minutes", "30")
        await _alert(update, f"ℹ️ الفترة الحالية: {current} دقيقة.\nالاستخدام: /setdelay <دقائق>")
        return
    try:
        minutes = int(context.args[0])
        if minutes < 1:
            raise ValueError
    except ValueError:
        await _alert(update, "❌ أدخل رقماً صحيحاً أكبر من صفر.")
        return

    await set_setting("post_delay_minutes", str(minutes))

    # Reschedule the APScheduler job immediately
    from scheduler import reschedule_auto_post
    reschedule_auto_post(context.bot, minutes)

    await _alert(update, f"⏱️ تم تغيير الفترة إلى كل {minutes} دقيقة.")


@admin_only
async def cmd_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /approval on   — require admin approval before each post
    /approval off  — auto-approve everything
    """
    if not context.args or context.args[0].lower() not in ("on", "off"):
        current = await get_setting("require_approval", "false")
        state   = "مفعّل ✅" if current == "true" else "موقوف ❌"
        await _alert(update, f"ℹ️ وضع الموافقة: {state}\nالاستخدام: /approval on  أو  /approval off")
        return

    enabled = context.args[0].lower() == "on"
    await set_setting("require_approval", "true" if enabled else "false")
    await _alert(
        update,
        "✅ وضع الموافقة مفعّل الآن — كل منشور يحتاج موافقتك قبل النشر."
        if enabled else
        "❌ وضع الموافقة موقوف — المنشورات تُضاف مباشرة للطابور."
    )


@admin_only
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate today's digest and offer to post it."""
    await _alert(update, "📝 جاري إعداد الملخص اليومي…")

    import aiosqlite
    from database import DB_PATH

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT arabic_text FROM post_queue "
            "WHERE status = 'posted' AND date(posted_at) = date('now')"
        )
        rows = await cur.fetchall()

    posts   = [r["arabic_text"] for r in rows]
    summary = await daily_summary(posts)

    if not summary:
        await _alert(update, "❌ لا توجد منشورات اليوم لإعداد ملخص منها.")
        return

    context.user_data["pending_summary"] = summary

    await update.message.reply_text(
        f"📋 <b>الملخص اليومي المقترح:</b>\n\n{summary}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 نشر الملخص",  callback_data="post_summary"),
            InlineKeyboardButton("❌ إلغاء",        callback_data="cancel_summary"),
        ]]),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  CALLBACK QUERY HANDLER (inline buttons)
# ─────────────────────────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Ignore button presses from anyone other than the admin
    if query.from_user.id != ADMIN:
        return

    data = query.data

    if data.startswith("approve_"):
        pid = int(data.split("_", 1)[1])
        await update_status(pid, "approved")
        await query.edit_message_text(f"✅ تم اعتماد المنشور #{pid}")

    elif data.startswith("reject_"):
        pid = int(data.split("_", 1)[1])
        await update_status(pid, "rejected")
        await query.edit_message_text(f"🗑️ تم رفض المنشور #{pid}")

    elif data.startswith("postnow_"):
        pid = int(data.split("_", 1)[1])
        await query.edit_message_text(f"⚡ جاري نشر المنشور #{pid}…")
        ok = await post_specific(context.bot, pid)
        await query.edit_message_text(
            f"✅ تم نشر المنشور #{pid}!" if ok else f"❌ فشل نشر المنشور #{pid}."
        )

    elif data.startswith("preview_"):
        pid  = int(data.split("_", 1)[1])
        post = await get_post(pid)
        if post:
            text = post["arabic_text"]
            # Send as a new message so the original card is preserved
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"👁 <b>منشور #{pid}:</b>\n\n{text}",
                parse_mode="HTML",
            )
        else:
            await query.answer("❌ المنشور غير موجود.", show_alert=True)

    elif data == "post_summary":
        summary = context.user_data.get("pending_summary", "")
        if summary:
            count = await broadcast(context.bot, summary)
            await query.edit_message_text(f"✅ تم نشر الملخص اليومي على {count} قناة!")
            context.user_data.pop("pending_summary", None)
        else:
            await query.edit_message_text("❌ لا يوجد ملخص جاهز.")

    elif data == "cancel_summary":
        context.user_data.pop("pending_summary", None)
        await query.edit_message_text("❌ تم إلغاء نشر الملخص.")


# ─────────────────────────────────────────────────────────────────────────────
#  BUILD APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

def build_app() -> Application:
    """Wire all handlers and return the configured Application."""
    if not config.BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not configured.")

    app = Application.builder().token(config.BOT_TOKEN).build()

    handlers = [
        CommandHandler("start",       cmd_start),
        CommandHandler("help",        cmd_help),
        CommandHandler("status",      cmd_status),
        CommandHandler("queue",       cmd_queue),
        CommandHandler("preview",     cmd_preview),
        CommandHandler("approve",     cmd_approve),
        CommandHandler("reject",      cmd_reject),
        CommandHandler("editpost",    cmd_editpost),
        CommandHandler("addpost",     cmd_addpost),
        CommandHandler("postnow",     cmd_postnow),
        CommandHandler("skippost",    cmd_skippost),
        CommandHandler("clearqueue",  cmd_clearqueue),
        CommandHandler("pause",       cmd_pause),
        CommandHandler("resume",      cmd_resume),
        CommandHandler("setdelay",    cmd_setdelay),
        CommandHandler("approval",    cmd_approval),
        CommandHandler("summary",     cmd_summary),
        CallbackQueryHandler(handle_callback),
    ]

    for h in handlers:
        app.add_handler(h)

    return app
