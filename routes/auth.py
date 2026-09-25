"""Login, passwords, user accounts, and role permissions."""
import time

from flask import Blueprint, flash, g, redirect, render_template, request, send_file, session, url_for

import reports
from core import (PERMS, ROLES, admin_required, audit, commit, ex, hash_password,
                  login_required, norm_cell, q, q1, read_table_file, safe_next, verify_password)

bp = Blueprint("auth", __name__)

_failures = {}  # (username, ip) -> [timestamps]
MAX_TRIES, WINDOW = 5, 300


def _locked(key):
    fails = [t for t in _failures.get(key, []) if time.time() - t < WINDOW]
    _failures[key] = fails
    return len(fails) >= MAX_TRIES


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("system.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        key = (username.lower(), request.remote_addr)
        if _locked(key):
            flash("Too many wrong attempts. Wait five minutes and try again.", "error")
            return render_template("login.html"), 429
        u = q1("SELECT * FROM users WHERE username=? AND active=1", (username,))
        if u and verify_password(u["password_hash"], request.form.get("password", "")):
            _failures.pop(key, None)
            nxt = safe_next(request.args.get("next"))
            session.clear()
            session["uid"] = u["id"]
            g.user = u
            audit("login", "")
            commit()
            return redirect(nxt or url_for("system.dashboard"))
        _failures.setdefault(key, []).append(time.time())
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/password", methods=["GET", "POST"])
@login_required
def password():
    forced = bool(g.user["must_change_password"])
    if request.method == "POST":
        cur, new, new2 = (request.form.get(k, "") for k in ("current", "new", "confirm"))
        if not verify_password(g.user["password_hash"], cur):
            flash("Your current password is not correct.", "error")
        elif len(new) < 6:
            flash("The new password must have at least 6 characters.", "error")
        elif new != new2:
            flash("The two new passwords do not match.", "error")
        elif new == cur:
            flash("Choose a password different from the current one.", "error")
        else:
            ex("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
               (hash_password(new), g.user["id"]))
            audit("password changed", "own password")
            commit()
            flash("Password changed.", "ok")
            return redirect(url_for("system.dashboard"))
    return render_template("password.html", forced=forced)


# ---- users ---------------------------------------------------------------
@bp.route("/users")
@admin_required
def users():
    rows = q("SELECT u.*, (SELECT COUNT(*) FROM teaching t WHERE t.teacher_id=u.id) AS n_assign "
             "FROM users u ORDER BY u.active DESC, u.role, u.full_name")
    head_teacher = next((u for u in rows if u["role"] == "head_teacher" and u["active"]),
                        next((u for u in rows if u["role"] == "head_teacher"), None))
    deputy = next((u for u in rows if u["role"] == "deputy_head_teacher" and u["active"]),
                  next((u for u in rows if u["role"] == "deputy_head_teacher"), None))
    return render_template("users.html", users=rows, roles=ROLES,
                           head_teacher=head_teacher, deputy=deputy)


def _admin_count(exclude=None):
    return q1("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND id<>?",
              (exclude or 0,))["n"]


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def user_form(uid=None):
    user = q1("SELECT * FROM users WHERE id=?", (uid,)) if uid else None
    if uid and not user:
        flash("User not found.", "error")
        return redirect(url_for("auth.users"))
    if request.method == "POST":
        f = request.form
        username = f.get("username", "").strip()
        full_name = f.get("full_name", "").strip()
        tsc_no = f.get("tsc_no", "").strip()
        role = f.get("role", "teacher")
        active = 1 if f.get("active") else 0
        errors = []
        if not username or " " in username:
            errors.append("Username is required and cannot contain spaces.")
        if not full_name:
            errors.append("Full name is required.")
        if role not in ROLES:
            errors.append("Choose a valid role.")
        if q1("SELECT id FROM users WHERE username=? AND id<>?", (username, uid or 0)):
            errors.append("That username is already used.")
        if tsc_no and q1("SELECT id FROM users WHERE UPPER(tsc_no)=UPPER(?) AND id<>?", (tsc_no, uid or 0)):
            errors.append("That TSC number is already registered to another teacher.")
        pw = f.get("password", "")
        if not user and len(pw) < 6:
            errors.append("Give the new user a temporary password of at least 6 characters.")
        if user and user["role"] == "admin" and (role != "admin" or not active) and _admin_count(uid) == 0:
            errors.append("There must be at least one active administrator.")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("user_form.html", user=f, uid=uid, roles=ROLES)
        if user:
            ex("UPDATE users SET username=?, full_name=?, tsc_no=?, initials=?, role=?, phone=?, active=? WHERE id=?",
               (username, full_name, tsc_no, f.get("initials", "").strip(), role, f.get("phone", "").strip(), active, uid))
            audit("user edited", f"{username} ({role})")
            flash("User updated.", "ok")
        else:
            ex("INSERT INTO users(username,full_name,tsc_no,initials,password_hash,role,phone,active,must_change_password) "
               "VALUES(?,?,?,?,?,?,?,?,1)",
               (username, full_name, tsc_no, f.get("initials", "").strip(), hash_password(pw), role,
                f.get("phone", "").strip(), active))
            audit("user created", f"{username} ({role})")
            flash("User created. They will be asked to choose a new password at first login.", "ok")
        commit()
        return redirect(url_for("auth.users"))
    req_role = request.args.get("role", "teacher")
    default_role = req_role if req_role in ROLES else "teacher"
    return render_template("user_form.html", user=user or {"role": default_role, "active": 1}, uid=uid, roles=ROLES)


@bp.route("/users/<int:uid>/reset", methods=["POST"])
@admin_required
def user_reset(uid):
    pw = request.form.get("password", "")
    u = q1("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        flash("User not found.", "error")
    elif len(pw) < 6:
        flash("The temporary password must have at least 6 characters.", "error")
    else:
        ex("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?", (hash_password(pw), uid))
        audit("password reset", u["username"])
        commit()
        flash(f"Password for {u['full_name']} reset. They must change it at next login.", "ok")
    return redirect(url_for("auth.user_form", uid=uid))


@bp.route("/users/<int:uid>/delete", methods=["POST"])
@admin_required
def user_delete(uid):
    user = q1("SELECT * FROM users WHERE id=?", (uid,))
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("auth.users"))
    if g.user["id"] == uid:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("auth.users"))
    if user["role"] == "admin" and _admin_count(uid) == 0:
        flash("There must be at least one active administrator.", "error")
        return redirect(url_for("auth.users"))

    # Cascade unassignments
    ex("DELETE FROM teaching WHERE teacher_id=?", (uid,))
    ex("UPDATE classes SET class_teacher_id=NULL WHERE class_teacher_id=?", (uid,))
    ex("UPDATE marks SET entered_by=NULL WHERE entered_by=?", (uid,))
    ex("DELETE FROM users WHERE id=?", (uid,))
    audit("user deleted", f"{user['username']} ({user['full_name']})")
    commit()
    entity = "Teacher" if user["role"] in ("teacher", "head_teacher", "deputy_head_teacher") else "User"
    flash(f"{entity} '{user['full_name']}' has been permanently deleted.", "ok")
    return redirect(url_for("auth.users"))


TEACHER_ALIASES = {
    "full_name": ("full name", "name", "teacher name", "teacher", "names", "staff name", "staff"),
    "tsc_no": ("tsc number", "tsc no", "tsc", "tsc_no", "tsc no.", "tsc #", "tscnum", "tsc registration", "tsc_number"),
    "username": ("username", "user name", "user", "login", "login id", "account", "email"),
    "phone": ("phone", "telephone", "mobile", "phone number", "contact", "cell"),
    "initials": ("initials", "code", "short", "abbrev"),
    "role": ("role", "user role", "type", "access level", "designation"),
    "password": ("temporary password", "temp password", "password", "default password", "pass", "pwd"),
}


def _gen_username(full_name, existing_unames):
    """Generate clean unique username from full name (e.g. Grace Wanjiku -> gwanjiku)."""
    import re
    clean = re.sub(r"[^a-zA-Z0-9\s]", "", full_name).strip().lower()
    parts = clean.split()
    if not parts:
        base = "teacher"
    elif len(parts) == 1:
        base = parts[0]
    else:
        base = f"{parts[0][0]}{parts[-1]}"
    base = re.sub(r"[^a-z0-9]", "", base) or "teacher"
    candidate = base
    counter = 1
    while candidate in existing_unames:
        candidate = f"{base}{counter}"
        counter += 1
    return candidate


def _gen_initials(full_name):
    """Generate 2-3 letter initials from full name."""
    import re
    clean = re.sub(r"[^a-zA-Z\s]", "", full_name).strip()
    parts = [p for p in clean.split() if p.lower() not in ("mr", "mrs", "miss", "ms", "dr", "prof", "rev")]
    if not parts:
        parts = clean.split()
    if not parts:
        return "T"
    initials = "".join(p[0].upper() for p in parts[:3])
    return initials[:4]


def _normalize_role(role_val, default_role):
    if not role_val:
        return default_role
    r = role_val.strip().lower()
    if "deputy" in r:
        return "deputy_head_teacher"
    if "head" in r or "principal" in r:
        return "head_teacher"
    if r in ("teacher", "teach", "tr"):
        return "teacher"
    if "exam" in r or "officer" in r:
        return "exam_officer"
    if "admin" in r:
        return "admin"
    if "view" in r or "read" in r:
        return "viewer"
    return default_role if default_role in ROLES else "teacher"


@bp.route("/users/export")
@admin_required
def users_export():
    from core import db
    data = reports.users_workbook(db())
    return send_file(data, as_attachment=True, download_name="users.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/users/template")
@admin_required
def users_template():
    data = reports.teachers_template()
    return send_file(data, as_attachment=True, download_name="teachers_template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/users/template.csv")
@admin_required
def users_template_csv():
    data = reports.teachers_template_csv()
    return send_file(data, as_attachment=True, download_name="teachers_template.csv",
                     mimetype="text/csv")


@bp.route("/users/credentials-download")
@admin_required
def users_credentials_download():
    creds = session.get("last_teacher_import") or []
    if not creds:
        flash("No recent import credentials found to download.", "error")
        return redirect(url_for("auth.users_import"))
    data = reports.teachers_credentials_workbook(creds)
    return send_file(data, as_attachment=True, download_name="teacher_login_credentials.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/users/import", methods=["GET", "POST"])
@admin_required
def users_import():
    report = None
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Choose an Excel (.xlsx) or CSV (.csv) file to upload.", "error")
            return render_template("users_import.html", report=None, roles=ROLES)
        fn_lower = file.filename.lower()
        if not fn_lower.endswith((".xlsx", ".xlsm", ".csv", ".tsv", ".txt")):
            flash("Unsupported file format. Please upload an Excel (.xlsx) or CSV (.csv) file.", "error")
            return render_template("users_import.html", report=None, roles=ROLES)
        update = bool(request.form.get("update"))
        default_role = request.form.get("default_role", "teacher")
        if default_role not in ROLES:
            default_role = "teacher"
        form_password = request.form.get("default_password", "").strip() or "Teacher@2026"
        force_change = bool(request.form.get("must_change_pw"))

        try:
            rows = read_table_file(file)
        except Exception as e:
            flash(f"That file could not be read: {e}", "error")
            return render_template("users_import.html", report=None, roles=ROLES)
        if not rows:
            flash("The uploaded file appears to be empty.", "error")
            return render_template("users_import.html", report=None, roles=ROLES)

        col, header_idx = {}, None
        for i, row in enumerate(rows[:10]):
            names = [norm_cell(c).lower() for c in row]
            found = {}
            for key, al in TEACHER_ALIASES.items():
                for j, n in enumerate(names):
                    if n in al and key not in found:
                        found[key] = j
            if "full_name" in found:
                col, header_idx = found, i
                break
        if header_idx is None:
            flash("Could not find the 'Full Name' column in the uploaded file. Please use the provided template.", "error")
            return render_template("users_import.html", report=None, roles=ROLES)

        existing_users = {r["username"].lower(): r for r in q("SELECT * FROM users")}
        existing_phones = {r["phone"].strip(): r for r in q("SELECT * FROM users WHERE phone<>''")}
        existing_tsc = {r["tsc_no"].strip().upper(): r for r in q("SELECT * FROM users WHERE tsc_no<>''")}
        seen_unames = set(existing_users.keys())

        def get(row, key):
            return norm_cell(row[col[key]]) if key in col and col[key] < len(row) else ""

        added = updated = skipped = 0
        problems = []
        credentials_list = []

        for n, row in enumerate(rows[header_idx + 1:], header_idx + 2):
            if not any(norm_cell(c) for c in row):
                continue
            full_name = get(row, "full_name").strip()
            if not full_name:
                problems.append(f"Row {n}: Full name is missing.")
                skipped += 1
                continue
            raw_uname = get(row, "username").strip().lower().replace(" ", "")
            tsc_no = get(row, "tsc_no").strip()
            phone = get(row, "phone").strip()
            initials = get(row, "initials").strip() or _gen_initials(full_name)
            role = _normalize_role(get(row, "role"), default_role)
            row_pw = get(row, "password").strip()
            pw = row_pw if len(row_pw) >= 6 else form_password

            existing = None
            if raw_uname and raw_uname in existing_users:
                existing = existing_users[raw_uname]
            elif tsc_no and tsc_no.upper() in existing_tsc:
                existing = existing_tsc[tsc_no.upper()]
            elif phone and phone in existing_phones:
                existing = existing_phones[phone]

            if existing:
                if update:
                    final_tsc = tsc_no or existing.get("tsc_no", "")
                    update_sql = "UPDATE users SET full_name=?, tsc_no=?, initials=?, role=?, phone=? "
                    args = [full_name, final_tsc, initials, role, phone]
                    if row_pw and len(row_pw) >= 6:
                        update_sql += ", password_hash=?, must_change_password=? "
                        args += [hash_password(row_pw), 1 if force_change else 0]
                    update_sql += "WHERE id=?"
                    args.append(existing["id"])
                    ex(update_sql, args)
                    updated += 1
                    credentials_list.append({
                        "name": full_name,
                        "tsc_no": final_tsc,
                        "username": existing["username"],
                        "password": row_pw if len(row_pw) >= 6 else "(Existing password unchanged)",
                        "role": ROLES.get(role, role),
                        "phone": phone,
                        "status": "Updated"
                    })
                else:
                    skipped += 1
                    problems.append(f"Row {n}: Teacher '{full_name}' ({existing['username']}) already exists (skipped).")
            else:
                uname = raw_uname if raw_uname and raw_uname not in seen_unames else _gen_username(full_name, seen_unames)
                seen_unames.add(uname)
                ex("INSERT INTO users(username, full_name, tsc_no, initials, password_hash, role, phone, active, must_change_password) "
                   "VALUES(?,?,?,?,?,?,?,?,?)",
                   (uname, full_name, tsc_no, initials, hash_password(pw), role, phone, 1, 1 if force_change else 0))
                added += 1
                record = {"username": uname, "phone": phone, "tsc_no": tsc_no}
                existing_users[uname] = record
                if tsc_no:
                    existing_tsc[tsc_no.upper()] = record
                if phone:
                    existing_phones[phone] = record
                credentials_list.append({
                    "name": full_name,
                    "tsc_no": tsc_no,
                    "username": uname,
                    "password": pw,
                    "role": ROLES.get(role, role),
                    "phone": phone,
                    "status": "Created"
                })

        audit("teachers imported", f"{added} added, {updated} updated, {skipped} skipped")
        commit()
        session["last_teacher_import"] = credentials_list
        report = {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "problems": problems[:60],
            "more": max(len(problems) - 60, 0),
            "credentials": credentials_list
        }
    return render_template("users_import.html", report=report, roles=ROLES)


# ---- roles ---------------------------------------------------------------
@bp.route("/roles", methods=["GET", "POST"])
@admin_required
def roles():
    editable = ["head_teacher", "deputy_head_teacher", "exam_officer", "teacher", "viewer"]
    if request.method == "POST":
        ex("DELETE FROM role_permissions")
        for role in editable:
            for perm, _ in PERMS:
                if request.form.get(f"{role}:{perm}"):
                    ex("INSERT INTO role_permissions(role,perm) VALUES(?,?)", (role, perm))
        audit("permissions changed", "role permissions saved")
        commit()
        flash("Permissions saved.", "ok")
        return redirect(url_for("auth.roles"))
    granted = {(r["role"], r["perm"]) for r in q("SELECT role, perm FROM role_permissions")}
    counts = {r["role"]: r["n"] for r in q("SELECT role, COUNT(*) n FROM users WHERE active=1 GROUP BY role")}
    return render_template("roles.html", perms=PERMS, editable=editable, roles=ROLES,
                           granted=granted, counts=counts)
