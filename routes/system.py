"""Dashboard, school settings, grading scale, backups, and the activity log."""
import math
import os

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_file, url_for)
from werkzeug.utils import secure_filename

import calc
from core import (BACKUP_DIR, CLASS_LABEL, DEFAULT_SETTINGS, GRADING_PRESETS, LOGO_PATH, admin_required,
                  audit, commit, db, ex, list_backups, login_required, make_backup, perm_required, q, q1,
                  restore_backup, set_setting, settings)

bp = Blueprint("system", __name__)


@bp.route("/")
@login_required
def dashboard():
    counts = {
        "students": q1("SELECT COUNT(*) n FROM students WHERE status='active'")["n"],
        "classes": q1("SELECT COUNT(*) n FROM classes")["n"],
        "subjects": q1("SELECT COUNT(*) n FROM subjects")["n"],
        "teachers": q1("SELECT COUNT(*) n FROM users WHERE active=1 AND role<>'admin'")["n"],
    }
    exam = q1("SELECT * FROM exams WHERE status='open' ORDER BY id DESC LIMIT 1") or \
        q1("SELECT * FROM exams ORDER BY id DESC LIMIT 1")
    progress = None
    my_papers = []
    if exam:
        exp = q1("SELECT COALESCE(SUM((SELECT COUNT(*) FROM students s WHERE s.class_id=p.class_id AND s.status='active')),0) n "
                 "FROM exam_papers p WHERE p.exam_id=?", (exam["id"],))["n"]
        got = q1("SELECT COUNT(*) n FROM marks WHERE exam_id=? AND (score IS NOT NULL OR absent=1)", (exam["id"],))["n"]
        progress = {"exam": exam, "pct": min(round(got / exp * 100), 100) if exp else 0, "expected": exp, "got": got}
        if g.user["role"] not in ("admin",):
            from core import teacher_papers
            mine = teacher_papers()
            if mine:
                from routes.exams import paper_progress
                my_papers = [p for p in paper_progress(exam["id"]) if (p["class_id"], p["subject_id"]) in mine]
    recent = q("SELECT * FROM audit_log ORDER BY id DESC LIMIT 12")
    backups = list_backups()[:1]
    return render_template("dashboard.html", counts=counts, exam=exam, progress=progress,
                           my_papers=my_papers, recent=recent, last_backup=backups[0] if backups else None)


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def school_settings():
    if request.method == "POST":
        checkboxes = ("show_subject_position", "require_verified")
        val = request.form.get("pass_mark", "").strip()
        try:
            if not (0 <= float(val) <= 100):
                raise ValueError
        except ValueError:
            flash("Pass mark must be a number between 0 and 100.", "error")
            return redirect(url_for("system.school_settings"))
        for key in DEFAULT_SETTINGS:
            if key in checkboxes:
                set_setting(key, "1" if request.form.get(key) else "0")
            else:
                set_setting(key, request.form.get(key, "").strip())
        file = request.files.get("logo")
        if file and file.filename:
            if not file.filename.lower().endswith((".png", ".jpg", ".jpeg")):
                flash("The logo must be a PNG or JPG image.", "error")
                return redirect(url_for("system.school_settings"))
            if file.filename.lower().endswith((".jpg", ".jpeg")):
                from PIL import Image as PILImage
                PILImage.open(file.stream).convert("RGB").save(LOGO_PATH, "PNG")
            else:
                file.save(LOGO_PATH)
        audit("settings updated", "school settings")
        commit()
        flash("Settings saved.", "ok")
        return redirect(url_for("system.school_settings"))
    return render_template("settings.html", s=settings(), has_logo=os.path.exists(LOGO_PATH))


@bp.route("/logo.png")
def logo_image():
    if not os.path.exists(LOGO_PATH):
        abort(404)
    return send_file(LOGO_PATH, mimetype="image/png")


@bp.route("/settings/logo/remove", methods=["POST"])
@admin_required
def logo_remove():
    if os.path.exists(LOGO_PATH):
        os.remove(LOGO_PATH)
        audit("logo removed", "")
        commit()
        flash("Logo removed.", "ok")
    return redirect(url_for("system.school_settings"))


# ---------------------------------------------------------------------------
# grading scale
# ---------------------------------------------------------------------------
@bp.route("/grading", methods=["GET", "POST"])
@admin_required
def grading():
    if request.method == "POST":
        preset = request.form.get("preset")
        if preset and preset in GRADING_PRESETS:
            from core import load_bands_into
            load_bands_into(db(), preset)
            audit("grading scale changed", f"preset: {preset}")
            commit()
            flash("Grading scale replaced.", "ok")
            return redirect(url_for("system.grading"))
        mins = request.form.getlist("min_pct")
        grades = request.form.getlist("grade")
        points = request.form.getlist("points")
        remarks = request.form.getlist("remark")
        comments = request.form.getlist("comment")
        rows = list(zip(mins, grades, points, remarks, comments))
        errors, parsed = [], []
        for i, (m, g_, p, r, c) in enumerate(rows, 1):
            if not g_.strip():
                continue
            try:
                m_v, p_v = float(m), float(p or 0)
            except ValueError:
                errors.append(f"Row {i}: minimum % and points must be numbers.")
                continue
            if not (0 <= m_v <= 100):
                errors.append(f"Row {i}: minimum % must be between 0 and 100.")
                continue
            parsed.append((m_v, g_.strip(), p_v, r.strip(), c.strip()))
        if not parsed:
            errors.append("Add at least one grade band.")
        parsed.sort(key=lambda x: -x[0])
        if parsed and parsed[-1][0] != 0:
            errors.append("The lowest grade band must start at 0%.")
        for i in range(len(parsed) - 1):
            if parsed[i][0] == parsed[i + 1][0]:
                errors.append(f"Two bands both start at {parsed[i][0]}% - each must be different.")
                break
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("grading.html", bands=calc.get_bands(db()), presets=GRADING_PRESETS, errors=True)
        ex("DELETE FROM grade_bands")
        for m_v, g_, p_v, r, c in parsed:
            ex("INSERT INTO grade_bands(min_pct,grade,points,remark,comment) VALUES(?,?,?,?,?)", (m_v, g_, p_v, r, c))
        audit("grading scale changed", "custom scale saved")
        commit()
        flash("Grading scale saved. This applies to all examinations, including past ones.", "ok")
        return redirect(url_for("system.grading"))
    return render_template("grading.html", bands=calc.get_bands(db()), presets=GRADING_PRESETS, errors=False)


# ---------------------------------------------------------------------------
# backup and restore
# ---------------------------------------------------------------------------
@bp.route("/backup")
@perm_required("backup.create")
def backup_home():
    return render_template("backup.html", backups=list_backups())


@bp.route("/backup/create", methods=["POST"])
@perm_required("backup.create")
def backup_create():
    path, note = make_backup("manual")
    audit("backup created", os.path.basename(path))
    commit()
    flash("Backup created." + (f" {note}" if note else ""), "ok" if not note else "error")
    return redirect(url_for("system.backup_home"))


@bp.route("/backup/download/<name>")
@perm_required("backup.create")
def backup_download(name):
    safe = secure_filename(name)
    path = os.path.join(BACKUP_DIR, safe)
    if not safe.endswith(".zip") or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=safe, mimetype="application/zip")


@bp.route("/backup/delete/<name>", methods=["POST"])
@admin_required
def backup_delete(name):
    safe = secure_filename(name)
    path = os.path.join(BACKUP_DIR, safe)
    if safe.endswith(".zip") and os.path.exists(path):
        os.remove(path)
        audit("backup deleted", safe)
        commit()
        flash("Backup file removed.", "ok")
    return redirect(url_for("system.backup_home"))


@bp.route("/backup/restore", methods=["POST"])
@admin_required
def backup_restore():
    file = request.files.get("file")
    if not file or not file.filename.lower().endswith(".zip"):
        flash("Choose a backup .zip file created by this system.", "error")
        return redirect(url_for("system.backup_home"))
    if request.form.get("confirm", "").strip().upper() != "RESTORE":
        flash("Type RESTORE in the box to confirm. This replaces all current data.", "error")
        return redirect(url_for("system.backup_home"))
    tmp = os.path.join(current_app.config["TMP_DIR"], "upload_restore.zip")
    file.save(tmp)
    try:
        restore_backup(tmp)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("system.backup_home"))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    flash("Data restored from backup. A safety copy of the data just before this restore was also saved.", "ok")
    return redirect(url_for("system.dashboard"))


# ---------------------------------------------------------------------------
# activity log
# ---------------------------------------------------------------------------
PER_PAGE = 60


@bp.route("/audit")
@perm_required("audit.view")
def audit_log():
    page = max(request.args.get("page", 1, type=int), 1)
    total = q1("SELECT COUNT(*) n FROM audit_log")["n"]
    rows = q("SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?", (PER_PAGE, (page - 1) * PER_PAGE))
    return render_template("audit.html", rows=rows, page=page, pages=max(math.ceil(total / PER_PAGE), 1), total=total)
