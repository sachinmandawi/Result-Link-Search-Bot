# -*- coding: utf-8 -*-
"""
Request Link Search Bot
- User sends any keyword / phrase to the bot.
- Bot searches a configured source group/channel and shows paginated results.
- User can "Forward this page" (forwards all messages on current page to the user privately)
  or open original message link (if available).
Requirements: pyrogram, tgcrypto
"""

import asyncio
import json
import logging
from uuid import uuid4
from typing import List, Dict

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
)

# ----------------- CONFIG: Replace these with your values -----------------
BOT_TOKEN = "8392371637:AAFUaicG1CH4IofZmn4maist4dpkyNPhjiM"
API_ID = 23292615            # int: your api_id from my.telegram.org
API_HASH = "fc15ff59f3a1d77e4d86ff6f3ded9d44"
SOURCE_CHAT = "2668553375"  # username or chat_id of the source group
RESULTS_PER_PAGE = 5
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Client("request_search_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# In-memory cache: search_key -> {"user_id": int, "ids": [message_id...], "source": source_chat}
# Using in-memory store so callback buttons can reference results without stuffing lots of data into callback.
SEARCH_CACHE: Dict[str, Dict] = {}

def build_results_keyboard(search_key: str, page: int, total_pages: int):
    data_prev = f"nav|{search_key}|{page-1}"
    data_next = f"nav|{search_key}|{page+1}"
    data_forward = f"fwd|{search_key}|{page}"
    kb = []
    # Prev / Next row
    row = []
    if page > 1:
        row.append(InlineKeyboardButton("◀ Prev", callback_data=data_prev))
    row.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        row.append(InlineKeyboardButton("Next ▶", callback_data=data_next))
    kb.append(row)
    # Forward this page
    kb.append([InlineKeyboardButton("➡ Forward this page", callback_data=data_forward)])
    # Cancel / Close
    kb.append([InlineKeyboardButton("❌ Close", callback_data=f"close|{search_key}")])
    return InlineKeyboardMarkup(kb)

async def format_preview_text(app: Client, source_chat: str, message_ids: List[int]) -> str:
    """
    Returns a short preview text showing each message index and first line.
    """
    lines = []
    for i, mid in enumerate(message_ids, start=1):
        try:
            m = await app.get_messages(source_chat, mid)
        except Exception:
            lines.append(f"{i}. [Message ID {mid}] — (couldn't fetch preview)")
            continue
        preview = None
        if m.text:
            preview = m.text.splitlines()[0][:120]
        elif m.caption:
            preview = m.caption.splitlines()[0][:120]
        else:
            if m.media:
                preview = f"<{m.media.__class__.__name__}>"
            else:
                preview = "(no text)"
        # Provide small indicator of type
        lines.append(f"{i}. {preview}")
    return "\n".join(lines)

@app.on_message(filters.private & filters.command("start"))
async def start_cmd(c: Client, m: Message):
    await m.reply_text(
        "✨ Welcome — Send any keyword and I'll search the source group for matching posts.\n\n"
        "Example: `Taylor Swift`, `movie 720p`, `file123`",
        quote=True
    )

@app.on_message(filters.private & filters.text & ~filters.command)
async def handle_search(c: Client, m: Message):
    query = m.text.strip()
    if not query:
        await m.reply_text("Please send a non-empty keyword to search.", quote=True)
        return

    searching_msg = await m.reply_text(f"🔎 Searching for: <b>{query}</b> …", quote=True, parse_mode="html")
    try:
        # Use client.search_messages to search the source chat for the query.
        # This returns a list of Message objects (async).
        results = await c.search_messages(chat_id=SOURCE_CHAT, query=query, limit=200)
    except Exception as e:
        log.exception("search_messages failed")
        await searching_msg.edit_text(f"❌ Search failed: {e}")
        return

    if not results:
        await searching_msg.edit_text("No results found in the source group.")
        return

    # Extract message_ids in the order returned
    msg_ids = [m_.message_id for m_ in results]

    # Create cache key for this user search
    search_key = str(uuid4())
    SEARCH_CACHE[search_key] = {"user_id": m.from_user.id, "ids": msg_ids, "source": SOURCE_CHAT}

    # Pagination
    total = len(msg_ids)
    per = RESULTS_PER_PAGE
    total_pages = (total + per - 1) // per
    page = 1

    start_idx = (page - 1) * per
    page_ids = msg_ids[start_idx:start_idx + per]

    preview_text = await format_preview_text(c, SOURCE_CHAT, page_ids)
    text = f"🔎 Results for: <b>{query}</b>\n\n{preview_text}\n\n<b>Total matches:</b> {total}"
    await searching_msg.edit_text(text, parse_mode="html", reply_markup=build_results_keyboard(search_key, page, total_pages))

@app.on_callback_query()
async def callbacks(c: Client, cq: CallbackQuery):
    data = cq.data or ""
    # noop: used for page label
    if data == "noop":
        await cq.answer()
        return

    parts = data.split("|")
    action = parts[0]
    if action == "nav":
        # nav|search_key|page
        _, search_key, page_s = parts
        page = int(page_s)
        entry = SEARCH_CACHE.get(search_key)
        if not entry or entry.get("user_id") != cq.from_user.id:
            await cq.answer("This search has expired or isn't yours.", show_alert=True)
            return
        ids = entry["ids"]
        total = len(ids)
        per = RESULTS_PER_PAGE
        total_pages = (total + per - 1) // per
        if page < 1 or page > total_pages:
            await cq.answer("Page out of range.", show_alert=True)
            return
        start_idx = (page - 1) * per
        page_ids = ids[start_idx:start_idx + per]
        preview_text = await format_preview_text(c, entry["source"], page_ids)
        new_text = f"🔎 Search results (page {page}/{total_pages})\n\n{preview_text}\n\n<b>Total matches:</b> {total}"
        await cq.message.edit_text(new_text, parse_mode="html", reply_markup=build_results_keyboard(search_key, page, total_pages))
        await cq.answer()
        return

    if action == "fwd":
        # fwd|search_key|page
        _, search_key, page_s = parts
        page = int(page_s)
        entry = SEARCH_CACHE.get(search_key)
        if not entry or entry.get("user_id") != cq.from_user.id:
            await cq.answer("This search has expired or isn't yours.", show_alert=True)
            return
        ids = entry["ids"]
        per = RESULTS_PER_PAGE
        start_idx = (page - 1) * per
        page_ids = ids[start_idx:start_idx + per]
        # Forward each message to the user (private chat where button was pressed)
        await cq.answer("Forwarding messages — please wait...", show_alert=False)
        succeeded = 0
        failed = 0
        for mid in page_ids:
            try:
                await c.forward_messages(chat_id=cq.from_user.id, from_chat_id=entry["source"], message_ids=mid)
                succeeded += 1
                # small sleep to avoid flood limits
                await asyncio.sleep(0.2)
            except Exception as e:
                log.exception("Failed to forward message %s", mid)
                failed += 1
        await cq.message.reply_text(f"✅ Forwarded: {succeeded}\n❌ Failed: {failed}")
        return

    if action == "close":
        _, search_key = parts
        # remove cache if present
        if search_key in SEARCH_CACHE:
            SEARCH_CACHE.pop(search_key, None)
        try:
            await cq.message.delete()
        except Exception:
            await cq.answer("Closed.")
        return

    # unknown action
    await cq.answer()

if __name__ == "__main__":
    print("Bot starting...")
    app.run()
