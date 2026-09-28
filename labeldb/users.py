"""Manage login accounts from the command line.

    python -m labeldb.users add friend@example.com     # prompts for a password
    python -m labeldb.users passwd friend@example.com  # change password, signs out everywhere
    python -m labeldb.users remove friend@example.com
    python -m labeldb.users list

Uses $LABEL_DB (or ./label.db), same as the app. On the server, use the
wrapper instead, which runs as the service user: `sudo labeldb users add ...`
"""

import argparse
import getpass
import sys

from labeldb import auth, db


def prompt_password():
    while True:
        pw = getpass.getpass("New password: ")
        if len(pw) < auth.MIN_PASSWORD_LENGTH:
            print(f"Use at least {auth.MIN_PASSWORD_LENGTH} characters.")
            continue
        if getpass.getpass("Repeat password: ") != pw:
            print("Passwords didn't match.")
            continue
        return pw


def main(argv=None, password_source=prompt_password):
    parser = argparse.ArgumentParser(description="Manage login accounts.")
    parser.add_argument("action", choices=["add", "passwd", "remove", "list"])
    parser.add_argument("email", nargs="?")
    args = parser.parse_args(argv)
    if args.action != "list" and not args.email:
        parser.error(f"{args.action} needs an email address")

    conn = db.connect()
    db.init_db(conn)
    email = (args.email or "").strip()

    if args.action == "list":
        for row in conn.execute("SELECT email FROM users ORDER BY email"):
            print(row["email"])
        return

    exists = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if args.action == "add":
        if exists:
            sys.exit(f"{email} already has an account. Use 'passwd' to change the password.")
        conn.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                     (email, auth.hash_password(password_source())))
        print(f"Added {email}.")
    elif not exists:
        sys.exit(f"No account for {email}.")
    elif args.action == "passwd":
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (auth.hash_password(password_source()), exists["id"]))
        # A password change should kick out any existing sessions.
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (exists["id"],))
        print(f"Password changed for {email}; they've been signed out everywhere.")
    elif args.action == "remove":
        conn.execute("DELETE FROM users WHERE id = ?", (exists["id"],))
        print(f"Removed {email}.")
    conn.commit()


if __name__ == "__main__":
    main()
