"""Password reset utility for Alara School Examination System.

Run this script anytime you need to reset an account password from the command line:
    python reset_password.py
    python reset_password.py <username> <new_password>
"""
import sys
import core


def main():
    core.init_db()
    con = core.connect()
    con.row_factory = core.sqlite3.Row
    users = con.execute("SELECT id, username, full_name, role FROM users ORDER BY username").fetchall()

    if not users:
        print("No users found in database.")
        con.close()
        return

    if len(sys.argv) >= 3:
        target_username = sys.argv[1].strip()
        new_pw = sys.argv[2].strip()
    else:
        print("\n=== Alara School System - Password Reset ===")
        print("Existing accounts:")
        for u in users:
            print(f"  - {u['username']} ({u['full_name']} · {u['role']})")
        print()
        target_username = input("Enter username to reset [admin]: ").strip() or "admin"
        new_pw = input("Enter new password [admin123]: ").strip() or "admin123"

    user = con.execute("SELECT id, username FROM users WHERE username=? COLLATE NOCASE", (target_username,)).fetchone()
    if not user:
        print(f"Error: User '{target_username}' not found.")
        con.close()
        return

    if len(new_pw) < 6:
        print("Error: Password must be at least 6 characters long.")
        con.close()
        return

    pw_hash = core.hash_password(new_pw)
    con.execute("UPDATE users SET password_hash=?, must_change_password=0, active=1 WHERE id=?", (pw_hash, user["id"]))
    con.commit()
    con.close()
    print(f"\n[OK] Password for '{user['username']}' has been reset to: {new_pw}")
    print("You can now open http://127.0.0.1:5000 and log in.\n")


if __name__ == "__main__":
    main()
