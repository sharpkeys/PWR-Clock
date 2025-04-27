import os
import logging
from datetime import datetime, timedelta, date
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# File-based storage (replace with proper database in production)
USERS_FILE = "users.json"
ENTRIES_FILE = "time_entries.json"

# Load data from files or initialize empty if files don't exist
def load_data():
    global users, time_entries
    try:
        with open(USERS_FILE, 'r') as f:
            users = json.load(f)
        # Convert string user IDs back to integers
        users = {int(k): v for k, v in users.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        users = {}

    try:
        with open(ENTRIES_FILE, 'r') as f:
            entries_data = json.load(f)

        # Convert string user IDs back to integers and parse datetime strings
        time_entries = {}
        for user_id, entries in entries_data.items():
            user_id = int(user_id)
            time_entries[user_id] = []
            for entry in entries:
                parsed_entry = {
                    'in_time': datetime.fromisoformat(entry['in_time']) if entry['in_time'] else None,
                    'out_time': datetime.fromisoformat(entry['out_time']) if entry['out_time'] else None
                }
                time_entries[user_id].append(parsed_entry)
    except (FileNotFoundError, json.JSONDecodeError):
        time_entries = {}

# Save data to files
def save_data():
    # Save users
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

    # Save time entries (convert datetime objects to strings)
    entries_data = {}
    for user_id, entries in time_entries.items():
        entries_data[str(user_id)] = []
        for entry in entries:
            serialized_entry = {
                'in_time': entry['in_time'].isoformat() if entry['in_time'] else None,
                'out_time': entry['out_time'].isoformat() if entry['out_time'] else None
            }
            entries_data[str(user_id)].append(serialized_entry)

    with open(ENTRIES_FILE, 'w') as f:
        json.dump(entries_data, f)

# Load data at startup
load_data()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    chat = update.effective_chat

    # Check if user is an admin in a group chat
    is_admin = False

    if chat.type in ["group", "supergroup"]:
        # Get chat admins
        chat_admins = await context.bot.get_chat_administrators(chat.id)
        admin_ids = [admin.user.id for admin in chat_admins]
        is_admin = user.id in admin_ids
    else:
        # In private chat, make the first registered user an admin
        is_admin = len(users) == 0

    users[user_id] = {
        'name': user.first_name,
        'full_name': f"{user.first_name} {user.last_name if user.last_name else ''}".strip(),
        'timezone': 'Africa/Lagos',  # Default timezone
        'is_admin': is_admin,
        'registered_date': datetime.now(pytz.UTC).isoformat(),
        'is_employee': not is_admin  # Admins are not employees by default
    }
    save_data()

    # Special message for admin
    admin_message = " You've been set as an admin!" if is_admin else ""

    await update.message.reply_text(
        f'Welcome {user.first_name}! You can now track your work hours.{admin_message}\n'
        'Use /clockin to start working and /clockout when you\'re done.\n'
        'Check /help for more commands.'
    )

async def clock_in(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    # Check if user is registered
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    # Skip clock-in for admins who are not employees
    if users[user_id].get('is_admin') and not users[user_id].get('is_employee', True):
        await update.message.reply_text(
            '👑 *Admin Mode*\n'
            'As an admin, you are not required to clock in/out.\n'
            'Use /togglemode if you want to track your hours.',
            parse_mode='Markdown'
        )
        return

    if user_id not in time_entries:
        time_entries[user_id] = []

    # Check if already clocked in
    if time_entries[user_id] and time_entries[user_id][-1].get('in_time') and not time_entries[user_id][-1].get('out_time'):
        # Format time in user's timezone
        user_tz = pytz.timezone(users[user_id]['timezone'])
        clock_in_time = time_entries[user_id][-1]['in_time'].astimezone(user_tz)
        time_str = clock_in_time.strftime('%H:%M:%S on %d %b %Y')

        await update.message.reply_text(f'You are already clocked in since {time_str}!')
        return

    # Create new time entry
    time_entries[user_id].append({
        'in_time': datetime.now(pytz.UTC),
        'out_time': None
    })
    save_data()

    # Create inline keyboard for quick clock-out
    keyboard = [
        [InlineKeyboardButton("⏱️ Clock Out", callback_data="clockout")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text('Successfully clocked in! ⏰', reply_markup=reply_markup)

async def clock_out(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    # Check if user is registered
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    # Skip clock-out for admins who are not employees
    if users[user_id].get('is_admin') and not users[user_id].get('is_employee', True):
        await update.message.reply_text(
            '👑 *Admin Mode*\n'
            'As an admin, you are not required to clock in/out.\n'
            'Use /togglemode if you want to track your hours.',
            parse_mode='Markdown'
        )
        return

    if user_id not in time_entries or not time_entries[user_id]:
        await update.message.reply_text('You haven\'t clocked in yet!')
        return

    last_entry = time_entries[user_id][-1]
    if last_entry.get('out_time'):
        await update.message.reply_text('You are already clocked out!')
        return

    last_entry['out_time'] = datetime.now(pytz.UTC)
    save_data()

    # Calculate duration
    duration = last_entry['out_time'] - last_entry['in_time']
    hours = duration.total_seconds() / 3600

    # Format times in user's timezone
    user_tz = pytz.timezone(users[user_id]['timezone'])
    clock_in_time = last_entry['in_time'].astimezone(user_tz)
    clock_out_time = last_entry['out_time'].astimezone(user_tz)

    # Create inline keyboard for report options
    keyboard = [
        [InlineKeyboardButton("📊 Today's Report", callback_data="report_today"),
         InlineKeyboardButton("⏱️ Clock In Again", callback_data="clockin")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f'Clocked out! \n'
        f'Started: {clock_in_time.strftime("%H:%M:%S")}\n'
        f'Ended: {clock_out_time.strftime("%H:%M:%S")}\n'
        f'Session duration: {hours:.2f} hours',
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button clicks"""
    query = update.callback_query

    # Always answer the callback query first
    try:
        await query.answer()
    except Exception as e:
        logger.error(f"Error answering callback query: {e}")

    user_id = query.from_user.id

    # Check if user is registered
    if user_id not in users:
        try:
            await query.message.reply_text('Please register first with /start command.')
        except Exception as e:
            logger.error(f"Error sending message: {e}")
        return

    # Create a fake update with the original message
    fake_update = Update(update_id=0, message=query.message)
    fake_update.message.from_user = query.from_user
    fake_update.effective_user = query.from_user

    # Initialize context.args if needed
    if not hasattr(context, 'args'):
        context.args = []

    try:
        # Process different button callbacks
        if query.data == "clockin":
            await clock_in(fake_update, context)

        elif query.data == "clockout":
            await clock_out(fake_update, context)

        elif query.data == "report_today":
            # Check if admin or employee
            if not users[user_id].get('is_employee', True) and not users[user_id].get('is_admin', False):
                await query.message.reply_text('Only employees can view personal reports.')
                return

            context.args = [datetime.now().strftime("%Y-%m-%d")]  # Today's date
            await report(fake_update, context)

        elif query.data == "report_week":
            # Check if admin or employee
            if not users[user_id].get('is_employee', True) and not users[user_id].get('is_admin', False):
                await query.message.reply_text('Only employees can view personal reports.')
                return

            # Get start and end dates for the current week
            today = datetime.now().date()
            start_of_week = today - timedelta(days=today.weekday())  # Monday
            end_of_week = start_of_week + timedelta(days=6)  # Sunday
            context.args = [start_of_week.strftime("%Y-%m-%d"), end_of_week.strftime("%Y-%m-%d")]
            await report(fake_update, context)

        elif query.data == "report_month":
            # Check if admin or employee
            if not users[user_id].get('is_employee', True) and not users[user_id].get('is_admin', False):
                await query.message.reply_text('Only employees can view personal reports.')
                return

            # Get start and end dates for the current month
            today = datetime.now().date()
            start_of_month = today.replace(day=1)
            # Get the last day of the month
            if today.month == 12:
                end_of_month = today.replace(day=31)
            else:
                end_of_month = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
            context.args = [start_of_month.strftime("%Y-%m-%d"), end_of_month.strftime("%Y-%m-%d")]
            await report(fake_update, context)

        elif query.data == "team_report":
            # Check if user is an admin
            if not users[user_id].get('is_admin', False):
                await query.message.reply_text('Only admins can view team reports.')
                return

            await team_report(fake_update, context)

        elif query.data.startswith("view_user_"):
            # Check if user is an admin
            if not users[user_id].get('is_admin', False):
                await query.message.reply_text('Only admins can view user reports.')
                return

            # Extract user_id from callback data
            target_user_id = int(query.data.split("_")[2])

            # Set up context for user report
            if not hasattr(context, 'user_data'):
                context.user_data = {}

            context.user_data["selected_user_id"] = target_user_id
            await user_report(fake_update, context)

        elif query.data == "confirm_clear_logs":
            # Check if user is an admin
            if not users[user_id].get('is_admin', False):
                await query.message.reply_text('Only admins can clear logs.')
                return

            await clear_logs(fake_update, context)

        elif query.data == "cancel_clear_logs":
            # Check if user is an admin
            if not users[user_id].get('is_admin', False):
                await query.message.reply_text('Only admins can use this function.')
                return

            await query.message.reply_text('❌ Clear logs operation cancelled.')

    except Exception as e:
        logger.error(f"Error in button handler: {e}")
        try:
            await query.message.reply_text('An error occurred. Please try again or use the command directly.')
        except:
            pass

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    # Check if user is registered
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    # Skip status for admins who are not employees
    if users[user_id].get('is_admin') and not users[user_id].get('is_employee', True):
        keyboard = [
            [InlineKeyboardButton("Team Report", callback_data="team_report")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            '👑 *Admin Mode*\n'
            'As an admin, you are not required to clock in/out.\n'
            'Use the button below to view team reports.',
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    if user_id not in time_entries or not time_entries[user_id]:
        # Create inline keyboard for quick clock-in
        keyboard = [
            [InlineKeyboardButton("⏱️ Clock In", callback_data="clockin")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            '📌 Status: *NOT STARTED*\n'
            'No time entries found. Use /clockin to start tracking.',
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    last_entry = time_entries[user_id][-1]
    user_tz = pytz.timezone(users[user_id]['timezone'])

    # Calculate today's total (not month total)
    today_start = datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_total_seconds = 0

    for entry in time_entries[user_id]:
        if entry['in_time'] >= today_start:
            if entry.get('out_time'):
                duration = entry['out_time'] - entry['in_time']
            elif entry == last_entry:  # Current active session
                duration = datetime.now(pytz.UTC) - entry['in_time']
            else:
                continue  # Skip incomplete past entries

            today_total_seconds += duration.total_seconds()

    today_total_hours = today_total_seconds / 3600

    # Format response based on current clock status
    if last_entry and not last_entry.get('out_time'):
        duration = datetime.now(pytz.UTC) - last_entry['in_time']
        hours = duration.total_seconds() / 3600
        clock_in_time = last_entry['in_time'].astimezone(user_tz)

        # Create inline keyboard for quick clock-out
        keyboard = [
            [InlineKeyboardButton("⏱️ Clock Out", callback_data="clockout")],
            [InlineKeyboardButton("📊 Today's Report", callback_data="report_today")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f'📌 Status: *CLOCKED IN* ✅\n'
            f'Started at: {clock_in_time.strftime("%H:%M:%S")}\n'
            f'Current session: {hours:.2f} hours\n'
            f'Today\'s total: {today_total_hours:.2f} hours',
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        # Create inline keyboard for quick clock-in
        keyboard = [
            [InlineKeyboardButton("⏱️ Clock In", callback_data="clockin")],
            [InlineKeyboardButton("📊 Today's Report", callback_data="report_today")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f'📌 Status: *CLOCKED OUT* ⏸️\n'
            f'Today\'s total: {today_total_hours:.2f} hours',
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    target_user_id = None

    # Check if user is registered
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    # Check if user is an employee or admin
    is_admin = users[user_id].get('is_admin', False)
    is_employee = users[user_id].get('is_employee', True)

    # Only allow employees or admins to see reports
    if not is_employee and not is_admin:
        await update.message.reply_text('Only employees can access their own reports.')
        return

    # Check if admin is requesting report for another user
    if is_admin and hasattr(context, 'user_data') and 'selected_user_id' in context.user_data:
        target_user_id = context.user_data['selected_user_id']
        # Clear the selection after use
        del context.user_data['selected_user_id']
    else:
        target_user_id = user_id

    # If admin is not an employee, redirect to team report
    if target_user_id == user_id and is_admin and not is_employee:
        await team_report(update, context)
        return

    if target_user_id not in time_entries or not time_entries[target_user_id]:
        await update.message.reply_text('No time entries found.')
        return

    # Parse date range
    today = datetime.now().date()
    start_date = today
    end_date = today

    # Handle command arguments
    if context.args:
        try:
            if len(context.args) == 1:
                # Single date provided
                start_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
                end_date = start_date
            elif len(context.args) >= 2:
                # Date range provided
                start_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
                end_date = datetime.strptime(context.args[1], "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text('Invalid date format. Please use YYYY-MM-DD.')
            return

    # Convert dates to datetime objects with timezone
    user_tz = pytz.timezone(users[target_user_id]['timezone'])
    start_datetime = datetime.combine(start_date, datetime.min.time())
    start_datetime = user_tz.localize(start_datetime).astimezone(pytz.UTC)

    end_datetime = datetime.combine(end_date, datetime.max.time())
    end_datetime = user_tz.localize(end_datetime).astimezone(pytz.UTC)

    # Calculate hours in the date range
    total_seconds = 0
    entries_in_range = []

    for entry in time_entries[target_user_id]:
        # Skip entries outside the range
        if entry['in_time'] > end_datetime or entry['in_time'] < start_datetime:
            continue

        if entry.get('out_time'):
            duration = entry['out_time'] - entry['in_time']
            display_entry = {
                'in': entry['in_time'].astimezone(user_tz).strftime("%H:%M:%S"),
                'out': entry['out_time'].astimezone(user_tz).strftime("%H:%M:%S"),
                'date': entry['in_time'].astimezone(user_tz).strftime("%Y-%m-%d"),
                'hours': duration.total_seconds() / 3600
            }
            total_seconds += duration.total_seconds()
        elif entry == time_entries[target_user_id][-1]:  # Current active session
            duration = datetime.now(pytz.UTC) - entry['in_time']
            display_entry = {
                'in': entry['in_time'].astimezone(user_tz).strftime("%H:%M:%S"),
                'out': '⌛ Active',
                'date': entry['in_time'].astimezone(user_tz).strftime("%Y-%m-%d"),
                'hours': duration.total_seconds() / 3600
            }
            total_seconds += duration.total_seconds()
        else:
            continue  # Skip incomplete past entries

        entries_in_range.append(display_entry)

    total_hours = total_seconds / 3600

    # Generate report text
    if target_user_id != user_id:
        user_name = users[target_user_id]['full_name']
        report_title = f"📊 *Report for {user_name}*"
    else:
        report_title = "📊 *Your Time Report*"

    if start_date == end_date:
        date_range_str = f"{start_date.strftime('%d %b %Y')}"
    else:
        date_range_str = f"{start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"

    report_text = f"{report_title}\n*Period:* {date_range_str}\n\n"

    if entries_in_range:
        # Group entries by date
        entries_by_date = {}
        for entry in entries_in_range:
            if entry['date'] not in entries_by_date:
                entries_by_date[entry['date']] = []
            entries_by_date[entry['date']].append(entry)

        # Add entries to report by date
        for date_str, date_entries in sorted(entries_by_date.items()):
            date_total = sum(entry['hours'] for entry in date_entries)
            report_text += f"*{date_str}*: {date_total:.2f} hours\n"
            for i, entry in enumerate(date_entries, 1):
                report_text += f"  {i}. {entry['in']} - {entry['out']}: {entry['hours']:.2f}h\n"
            report_text += "\n"

        report_text += f"*Total hours:* {total_hours:.2f}"
    else:
        report_text += "No time entries found for this period."

    # Create inline keyboard with report options
    keyboard = []

    # Add back button if viewing another user's report
    if target_user_id != user_id:
        keyboard.append([InlineKeyboardButton("⬅️ Back to Team", callback_data="team_report")])

    keyboard.append([
        InlineKeyboardButton("Today", callback_data="report_today"),
        InlineKeyboardButton("This Week", callback_data="report_week"),
        InlineKeyboardButton("This Month", callback_data="report_month")
    ])

    # Add team report option for admins
    if is_admin and target_user_id == user_id:
        keyboard.append([InlineKeyboardButton("👥 Team Report", callback_data="team_report")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(report_text, reply_markup=reply_markup, parse_mode='Markdown')

async def user_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate report for a specific user (used by admins)"""
    user_id = update.effective_user.id

    # Check if user is registered and an admin
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    if not users[user_id].get('is_admin', False):
        await update.message.reply_text('This command is for admins only.')
        return

    if not hasattr(context, 'user_data') or 'selected_user_id' not in context.user_data:
        await update.message.reply_text('No user selected. Please use team report first.')
        return

    # Set up context.args for the report function
    if not hasattr(context, 'args'):
        context.args = []

    await report(update, context)

async def handle_text_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text-based confirmations for commands like clear_logs"""
    text = update.message.text.lower()
    user_id = update.effective_user.id

    # Check if user is registered and an admin
    if user_id not in users:
        return

    if not users[user_id].get('is_admin', False):
        return

    # Check if waiting for confirmation
    if not hasattr(context, 'user_data') or not context.user_data.get('confirm_clear'):
        return

    # Process confirmation for clear logs
    if text in ["yes", "y", "confirm", "yes, clear all logs", "yes, clear logs"]:
        # Reset time entries for all users
        for uid in time_entries:
            time_entries[uid] = []

        # Save the cleared data
        save_data()

        # Clear the confirmation flag
        context.user_data.pop('confirm_clear', None)

        await update.message.reply_text('✅ All time logs have been cleared. Everyone starts fresh now!')

    elif text in ["no", "n", "cancel", "no, cancel"]:
        # Clear the confirmation flag
        context.user_data.pop('confirm_clear', None)

        await update.message.reply_text('❌ Clear logs operation cancelled.')

async def check_idle_users(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check for users who have been clocked in for more than 12 hours and send a reminder"""
    now = datetime.now(pytz.UTC)
    idle_threshold = timedelta(hours=12)

    for user_id, entries in time_entries.items():
        if entries and not entries[-1].get('out_time'):
            duration = now - entries[-1]['in_time']
            if duration > idle_threshold:
                try:
                    user_tz = pytz.timezone(users[user_id]['timezone'])
                    clock_in_time = entries[-1]['in_time'].astimezone(user_tz).strftime("%H:%M:%S on %d %b %Y")
                    hours = duration.total_seconds() / 3600

                    # Create inline keyboard for quick clock-out
                    keyboard = [
                        [InlineKeyboardButton("⏱️ Clock Out", callback_data="clockout")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ *Idle Warning*\nYou've been clocked in for {hours:.1f} hours (since {clock_in_time}).\n"
                             f"Did you forget to clock out?",
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Failed to send idle reminder to user {user_id}: {e}")

async def check_idle_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual command to check for idle users (admin only)"""
    user_id = update.effective_user.id

    # Check if user is an admin
    if user_id not in users or not users[user_id].get('is_admin'):
        await update.message.reply_text('This command is for admins only.')
        return

    # Create fake context for idle check
    class FakeContext:
        def __init__(self, bot):
            self.bot = bot

    fake_context = FakeContext(context.bot)
    await check_idle_users(fake_context)
    await update.message.reply_text('✅ Idle user check completed.')

async def clear_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear time logs for all users (admin only)"""
    user_id = update.effective_user.id
    chat = update.effective_chat

    # Check if user is registered
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    # Always make user admin in private chat for testing
    if chat.type == "private":
        users[user_id]['is_admin'] = True
        save_data()

    if not users[user_id].get('is_admin', False):
        await update.message.reply_text('⚠️ This command is for admins only.')
        return

    # If this is a confirmation callback, proceed with clearing
    if hasattr(context, 'user_data') and context.user_data.get('confirm_clear'):
        # Reset time entries for all users
        for user_id in time_entries:
            time_entries[user_id] = []

        # Save the cleared data
        save_data()

        # Clear the confirmation flag
        if hasattr(context, 'user_data'):
            context.user_data.pop('confirm_clear', None)

        await update.message.reply_text('✅ All time logs have been cleared. Everyone starts fresh now!')
        return

    # Show confirmation dialog with text instructions
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, clear all logs", callback_data="confirm_clear_logs"),
            InlineKeyboardButton("❌ No, cancel", callback_data="cancel_clear_logs")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        '⚠️ *WARNING: This will delete ALL time logs for ALL users!*\n\n'
        'This action cannot be undone. Are you sure you want to proceed?\n\n'
        'If buttons don\'t work, simply reply with "yes" to confirm or "no" to cancel.',
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

    # Set confirmation flag
    if not hasattr(context, 'user_data'):
        context.user_data = {}
    context.user_data['confirm_clear'] = True

async def force_clear_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Emergency command to force clear all logs by directly deleting the file"""
    user_id = update.effective_user.id
    chat = update.effective_chat

    # Always make user admin in private chat
    if chat.type == "private":
        users[user_id]['is_admin'] = True
        save_data()

    if not users[user_id].get('is_admin', False):
        await update.message.reply_text('⚠️ This command is for admins only.')
        return

    try:
        # Delete the time entries file directly
        import os
        if os.path.exists(ENTRIES_FILE):
            os.remove(ENTRIES_FILE)
            global time_entries
            time_entries = {}
            await update.message.reply_text('🔥 Emergency log clear completed. All time entries have been deleted.')
        else:
            await update.message.reply_text('No time entries file found.')
    except Exception as e:
        await update.message.reply_text(f'Error clearing logs: {str(e)}')

async def toggle_employee_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle employee status for admins"""
    user_id = update.effective_user.id

    # Check if user is registered and an admin
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    if not users[user_id].get('is_admin', False):
        await update.message.reply_text('This command is for admins only.')
        return

    # Toggle status
    current_status = users[user_id].get('is_employee', False)
    users[user_id]['is_employee'] = not current_status
    save_data()

    if users[user_id]['is_employee']:
        await update.message.reply_text('✅ You are now in Employee Mode. Your work hours will be tracked.')
    else:
        await update.message.reply_text('👑 You are now in Admin Mode. Your work hours will not be tracked.')

async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set user timezone"""
    user_id = update.effective_user.id

    # Check if user is registered
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    # Check if timezone is provided
    if not context.args:
        current_timezone = users[user_id]['timezone']
        await update.message.reply_text(
            f'Your current timezone is: {current_timezone}\n'
            f'To change, use /timezone followed by a timezone name, e.g.,\n'
            f'/timezone Europe/London\n'
            f'/timezone America/New_York\n'
            f'/timezone Asia/Tokyo'
        )
        return

    # Try to set the timezone
    try:
        new_timezone = context.args[0]
        pytz.timezone(new_timezone)  # Validate timezone
        users[user_id]['timezone'] = new_timezone
        save_data()
        await update.message.reply_text(f'Timezone updated to: {new_timezone}')
    except pytz.exceptions.UnknownTimeZoneError:
        await update.message.reply_text(
            f'Unknown timezone: {context.args[0]}\n'
            f'Please use a valid timezone name, e.g.,\n'
            f'Africa/Lagos, Europe/London, America/New_York, Asia/Tokyo'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    is_admin = user_id in users and users[user_id].get('is_admin', False)

    if is_admin:
        help_text = """
*📋 Available Commands:*

*For Everyone:*
/start - Register as a user
/status - Check current status
/help - Show this help message
/timezone - Set your timezone

*For Employees:*
/clockin - Start work session
/clockout - End work session
/report - Get work hours report

*For Admins:*
/team - View team report
/checkidle - Check for idle users
/togglemode - Switch between admin/employee mode
/clearlogs - Reset all time entries (fresh start)
/forceclear - Emergency command to delete all logs

*Report Examples:*
/report - Report for today
/report 2025-04-20 - Report for specific date
/report 2025-04-01 2025-04-30 - Report for date range
/team 2025-04-01 2025-04-30 - Team report for date range
"""
    else:
        help_text = """
*📋 Available Commands:*

/start - Register as a user
/clockin - Start your work session
/clockout - End your work session
/status - Check your current status
/report - Get work hours report
/timezone - Set your timezone
/help - Show this help message

*Examples:*
/report - Report for today
/report 2025-04-20 - Report for specific date
/report 2025-04-01 2025-04-30 - Report for date range
"""

    help_text += """
*Tips:*
• Dates should be in YYYY-MM-DD format
• You'll get a reminder if clocked in >12 hours
• Use the buttons for quick actions
• If buttons don't work, you can type "yes" or "no" for confirmations
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def team_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a team report for admins"""
    user_id = update.effective_user.id

    # Check if user is registered and an admin
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    if not users[user_id].get('is_admin', False):
        await update.message.reply_text('⚠️ This command is for admins only.')
        return

    # Parse date range
    today = datetime.now().date()
    start_date = today
    end_date = today

    # Handle command arguments
    if context.args:
        try:
            if len(context.args) == 1:
                # Single date provided
                start_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
                end_date = start_date
            elif len(context.args) >= 2:
                # Date range provided
                start_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
                end_date = datetime.strptime(context.args[1], "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text('Invalid date format. Please use YYYY-MM-DD.')
            return

    # Get user timezone
    user_tz = pytz.timezone(users[user_id]['timezone'])
    start_datetime = datetime.combine(start_date, datetime.min.time())
    start_datetime = user_tz.localize(start_datetime).astimezone(pytz.UTC)

    end_datetime = datetime.combine(end_date, datetime.max.time())
    end_datetime = user_tz.localize(end_datetime).astimezone(pytz.UTC)

    # Calculate hours for each employee
    employee_stats = []

    for emp_id, emp_entries in time_entries.items():
        # Skip non-employees or admins not set as employees
        if emp_id not in users or not users[emp_id].get('is_employee', True):
            continue

        total_seconds = 0
        daily_entries = 0

        for entry in emp_entries:
            # Skip entries outside the range
            if entry['in_time'] > end_datetime or entry['in_time'] < start_datetime:
                continue

            if entry.get('out_time'):
                duration = entry['out_time'] - entry['in_time']
                total_seconds += duration.total_seconds()
                daily_entries += 1
            elif entry == emp_entries[-1]:  # Current active session
                duration = datetime.now(pytz.UTC) - entry['in_time']
                total_seconds += duration.total_seconds()
                daily_entries += 1

        # Only include if they have entries in the date range
        if total_seconds > 0:
            employee_stats.append({
                'user_id': emp_id,
                'name': users[emp_id].get('full_name', users[emp_id].get('name', 'Unknown')),
                'hours': total_seconds / 3600,
                'entries': daily_entries,
                'active': emp_entries and not emp_entries[-1].get('out_time')
            })

    # Sort employees by hours worked (highest to lowest)
    employee_stats.sort(key=lambda x: x['hours'], reverse=True)

    # Generate report text
    if start_date == end_date:
        date_range_str = f"{start_date.strftime('%d %b %Y')}"
    else:
        date_range_str = f"{start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"

    report_text = f"👥 *Team Time Report*\n*Period:* {date_range_str}\n\n"

    if employee_stats:
        for i, emp in enumerate(employee_stats, 1):
            status_emoji = "✅" if emp['active'] else "⏸️"
            report_text += f"{i}. {emp['name']}: *{emp['hours']:.2f}h* {status_emoji}\n"

        # Add total team hours
        total_team_hours = sum(emp['hours'] for emp in employee_stats)
        report_text += f"\n*Total team hours:* {total_team_hours:.2f}"
    else:
        report_text += "No time entries found for this period."

    # Create inline keyboard with report options and user buttons
    keyboard = [
        [
            InlineKeyboardButton("Today", callback_data="report_today"),
            InlineKeyboardButton("This Week", callback_data="report_week"),
            InlineKeyboardButton("This Month", callback_data="report_month")
        ]
    ]

    # Add buttons for viewing individual employee reports
    if employee_stats:
        report_text += "\n\nSelect an employee for detailed report:"

        for emp in employee_stats[:5]:  # Limit to top 5 to avoid button limit
            keyboard.append([InlineKeyboardButton(
                f"👤 {emp['name']} ({emp['hours']:.2f}h)",
                callback_data=f"view_user_{emp['user_id']}"
            )])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(report_text, reply_markup=reply_markup, parse_mode='Markdown')

def main() -> None:
    # Get token from environment variable
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("No TELEGRAM_BOT_TOKEN found in environment variables!")
        return

    # Build application
    application = Application.builder().token(token).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clockin", clock_in))
    application.add_handler(CommandHandler("clockout", clock_out))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("report", report))
    application.add_handler(CommandHandler("team", team_report))
    application.add_handler(CommandHandler("checkidle", check_idle_command))
    application.add_handler(CommandHandler("togglemode", toggle_employee_status))
    application.add_handler(CommandHandler("timezone", set_timezone))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("clearlogs", clear_logs))
    application.add_handler(CommandHandler("forceclear", force_clear_logs))  # Emergency command

    # Add handler for text confirmation (for when buttons don't work)
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_text_confirmation
    ))

    # Add callback query handler for buttons
    application.add_handler(CallbackQueryHandler(button_handler))

    # Try to add job for idle user checks if job queue is available
    try:
        job_queue = application.job_queue
        if job_queue:
            job_queue.run_repeating(check_idle_users, interval=3600, first=10)
        else:
            logger.warning("JobQueue not available. Idle user checks won't run automatically.")
    except Exception as e:
        logger.warning(f"Could not set up job queue: {e}. Idle user checks won't run automatically.")

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()

async def team_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a team report for admins"""
    user_id = update.effective_user.id

    # Check if user is registered and an admin
    if user_id not in users:
        await update.message.reply_text('Please register first with /start command.')
        return

    if not users[user_id].get('is_admin', False):
        await update.message.reply_text('⚠️ This command is for admins only.')
        return

    # Parse date range
    today = datetime.now().date()
    start_date = today
    end_date = today

    # Handle command arguments
    if context.args:
        try:
            if len(context.args) == 1:
                # Single date provided
                start_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
                end_date = start_date
            elif len(context.args) >= 2:
                # Date range provided
                start_date = datetime.strptime(context.args[0], "%Y-%m-%d").date()
                end_date = datetime.strptime(context.args[1], "%Y-%m-%d").date()
        except ValueError:
            await update.message.reply_text('Invalid date format. Please use YYYY-MM-DD.')
            return

    # Get user timezone
    user_tz = pytz.timezone(users[user_id]['timezone'])
    start_datetime = datetime.combine(start_date, datetime.min.time())
    start_datetime = user_tz.localize(start_datetime).astimezone(pytz.UTC)

    end_datetime = datetime.combine(end_date, datetime.max.time())
    end_datetime = user_tz.localize(end_datetime).astimezone(pytz.UTC)

    # Calculate hours for each employee
    employee_stats = []

    for emp_id, emp_entries in time_entries.items():
        # Skip non-employees or admins not set as employees
        if emp_id not in users or not users[emp_id].get('is_employee', True):
            continue

        total_seconds = 0
        daily_entries = 0

        for entry in emp_entries:
            # Skip entries outside the range
            if entry['in_time'] > end_datetime or entry['in_time'] < start_datetime:
                continue

            if entry.get('out_time'):
                duration = entry['out_time'] - entry['in_time']
                total_seconds += duration.total_seconds()
                daily_entries += 1
            elif entry == emp_entries[-1]:  # Current active session
                duration = datetime.now(pytz.UTC) - entry['in_time']
                total_seconds += duration.total_seconds()
                daily_entries += 1

        # Only include if they have entries in the date range
        if total_seconds > 0:
            employee_stats.append({
                'user_id': emp_id,
                'name': users[emp_id].get('full_name', users[emp_id].get('name', 'Unknown')),
                'hours': total_seconds / 3600,
                'entries': daily_entries,
                'active': emp_entries and not emp_entries[-1].get('out_time')
            })

    # Sort employees by hours worked (highest to lowest)
    employee_stats.sort(key=lambda x: x['hours'], reverse=True)

    # Generate report text
    if start_date == end_date:
        date_range_str = f"{start_date.strftime('%d %b %Y')}"
    else:
        date_range_str = f"{start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"

    report_text = f"👥 *Team Time Report*\n*Period:* {date_range_str}\n\n"

    if employee_stats:
        for i, emp in enumerate(employee_stats, 1):
            status_emoji = "✅" if emp['active'] else "⏸️"
            report_text += f"{i}. {emp['name']}: *{emp['hours']:.2f}h* {status_emoji}\n"

        # Add total team hours
        total_team_hours = sum(emp['hours'] for emp in employee_stats)
        report_text += f"\n*Total team hours:* {total_team_hours:.2f}"
    else:
        report_text += "No time entries found for this period."

    # Create inline keyboard with report options and user buttons
    keyboard = [
        [
            InlineKeyboardButton("Today", callback_data="report_today"),
            InlineKeyboardButton("This Week", callback_data="report_week"),
            InlineKeyboardButton("This Month", callback_data="report_month")
        ]
    ]

    # Add buttons for viewing individual employee reports
    if employee_stats:
        report_text += "\n\nSelect an employee for detailed report:"

        for emp in employee_stats[:5]:  # Limit to top 5 to avoid button limit
            keyboard.append([InlineKeyboardButton(
                f"👤 {emp['name']} ({emp['hours']:.2f}h)",
                callback_data=f"view_user_{emp['user_id']}"
            )])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(report_text, reply_markup=reply_markup, parse_mode='Markdown')