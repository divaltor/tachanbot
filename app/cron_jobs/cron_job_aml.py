import os
import sqlite3
from datetime import date

import telebot
from dateutil.relativedelta import relativedelta, MO

bot = telebot.TeleBot(os.getenv("BOT_TOKEN"))
memes_chat_id = int(os.getenv("MEMES_CHAT_ID"))
flood_thread_id = int(os.getenv("FLOOD_THREAD_ID", 1))
memes_thread_id = int(os.getenv("MEMES_THREAD_ID", 1))
chat_creator = 43529628
minimum_memes_count = 5
db_path = os.path.join(
    os.path.abspath(__file__).rsplit(os.sep, maxsplit=3)[0], "memes.db"
)
conn = sqlite3.connect(db_path, check_same_thread=False)
conn.execute(
    "CREATE TABLE IF NOT EXISTS dank_boss_titles (user_id int PRIMARY KEY, old_title string, assigned_at timestamp, old_tag string DEFAULT '', promoted int DEFAULT 0);"
)
existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(dank_boss_titles)")}
if "old_tag" not in existing_columns:
    conn.execute("ALTER TABLE dank_boss_titles ADD COLUMN old_tag string DEFAULT ''")
    conn.execute("ALTER TABLE dank_boss_titles ADD COLUMN promoted int DEFAULT 0")
    conn.commit()

# Telegram only lets the bot demote an admin when every right is False
admin_rights = [
    "can_post_messages",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_chat",
    "can_manage_video_chats",
    "can_manage_voice_chats",
    "can_manage_topics",
]
admin_title = "дух"


def promote_to_admin(user_id, username):
    user_data = bot.get_chat_member(memes_chat_id, user_id)
    if user_data.status == "administrator":
        return
    bot.promote_chat_member(
        memes_chat_id, user_id, **{right: True for right in admin_rights}
    )
    bot.set_chat_administrator_custom_title(memes_chat_id, user_id, admin_title)
    bot.send_message(
        memes_chat_id,
        "Чествуем новых админов! [{}](tg://user?id={})".format(username, user_id),
    )


def restore_previous_boss_title(new_boss_user_id, honored_user_ids):
    rows = conn.execute(
        "SELECT user_id, old_title, old_tag, promoted FROM dank_boss_titles"
    ).fetchall()
    for previous_boss_id, old_title, old_tag, promoted in rows:
        if new_boss_user_id and previous_boss_id == new_boss_user_id:
            continue
        try:
            status = bot.get_chat_member(memes_chat_id, previous_boss_id).status
            if status == "administrator":
                if not promoted:
                    bot.set_chat_administrator_custom_title(
                        memes_chat_id, previous_boss_id, old_title or ""
                    )
                elif previous_boss_id in honored_user_ids:
                    # Finished in the top 3 again, so stays an admin like other medalists
                    bot.set_chat_administrator_custom_title(
                        memes_chat_id, previous_boss_id, admin_title
                    )
                else:
                    bot.promote_chat_member(
                        memes_chat_id,
                        previous_boss_id,
                        **{right: False for right in admin_rights},
                    )
                    status = "member"
            if promoted and old_tag and status in ("member", "restricted"):
                bot.set_chat_member_tag(memes_chat_id, previous_boss_id, old_tag)
            # Keep the row on failure so the title is restored on the next run
            conn.execute(
                "DELETE FROM dank_boss_titles WHERE user_id = ?",
                (previous_boss_id,),
            )
            conn.commit()
        except Exception as err:
            print(err)


def save_boss_old_title(user_id):
    row = conn.execute(
        "SELECT user_id FROM dank_boss_titles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row:
        return
    member = bot.get_chat_member(memes_chat_id, user_id)
    is_admin = member.status == "administrator"
    # A regular member has a tag instead of an admin title; the bot promotes them for the week
    old_title = (member.custom_title or "") if is_admin else ""
    old_tag = "" if is_admin else (getattr(member, "tag", None) or "")
    conn.execute(
        "INSERT OR REPLACE INTO dank_boss_titles (user_id, old_title, assigned_at, old_tag, promoted) VALUES(?, ?, ?, ?, ?);",
        (user_id, old_title, date.today(), old_tag, int(not is_admin)),
    )
    conn.commit()


def main():
    today = date.today()
    last_monday = today + relativedelta(weekday=MO(-2))
    query = "SELECT user_id, username, ROUND(CAST((SUM(up_votes) - SUM(down_votes)) as float) / CAST(COUNT(*) as float), 3),  SUM(up_votes), COUNT(*) FROM memes_posts_v2 WHERE created_at > ? GROUP BY user_id, username ORDER BY ROUND(CAST((SUM(up_votes) - SUM(down_votes)) as float) / CAST(COUNT(*) as float), 3) DESC"
    rows = conn.execute(query, (last_monday,)).fetchall()
    msg = ["AML - Average Meme Likes\n"]
    stack = ["🥉", "🥈", "🥇"]
    gold_user_id = None
    gold_username = None
    low_memes_count = []
    honored_user_ids = set()
    for row in rows:
        user_id, username, aml, total_up_votes, total_count = row
        reward = "🤡"
        if stack and total_count >= minimum_memes_count:
            reward = stack.pop()
            if not gold_user_id and not gold_username:
                gold_username = username
                gold_user_id = user_id
            honored_user_ids.add(user_id)
            # The boss is promoted below, once their old title or tag is saved
            if user_id != gold_user_id:
                try:
                    promote_to_admin(user_id, username)
                except Exception as err:
                    print(err)
        message = "[{username}](tg://user?id={user_id}) - {aml} - {reward} (total up votes {total_up_votes}, total memes count {total_count})".format(
            username=username,
            user_id=user_id,
            aml=aml,
            reward=reward,
            total_up_votes=total_up_votes,
            total_count=total_count,
        )
        if total_count < minimum_memes_count:
            low_memes_count.append(message)
        else:
            msg.append(message)

    msg.append(
        "\n\n\nПользователи у которых <{} мемов в неделю не учавствуют в рейтинге\n\n".format(
            minimum_memes_count
        )
    )
    for message in low_memes_count:
        msg.append(message)

    bot.send_message(
        memes_chat_id,
        "\n".join(msg),
        message_thread_id=flood_thread_id,
        parse_mode="Markdown",
    )
    restore_previous_boss_title(gold_user_id, honored_user_ids)
    if not gold_user_id:
        return
    try:
        if gold_user_id == chat_creator:
            bot.send_message(
                memes_chat_id,
                "Нельзя присвоить титул создателю чата, присвой себе сам [{}](tg://user?id={})".format(
                    gold_username, gold_user_id
                ),
                message_thread_id=flood_thread_id,
                parse_mode="Markdown",
            )
        else:
            save_boss_old_title(gold_user_id)
            promote_to_admin(gold_user_id, gold_username)
            bot.set_chat_administrator_custom_title(
                chat_id=memes_chat_id,
                user_id=gold_user_id,
                custom_title="Dank boss",
            )
    except Exception as err:
        bot.send_message(
            memes_chat_id,
            "Ошибка, error: {}".format(err),
            message_thread_id=flood_thread_id,
            parse_mode="Markdown",
        )
        return
    bot.send_message(
        memes_chat_id,
        "Почет и уважение новому босу данка на эту неделю! [{}](tg://user?id={})".format(
            gold_username, gold_user_id
        ),
        message_thread_id=flood_thread_id,
        parse_mode="Markdown",
    )


if __name__ == "__main__":
    main()
