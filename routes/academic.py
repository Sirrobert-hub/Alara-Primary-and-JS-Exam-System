"""Students, classes and streams, subjects, and teaching assignments."""
import io
import math

import openpyxl
from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

import reports
from core import (CLASS_LABEL, audit, commit, db, ex, flash_errors, is_admin, norm_cell, perm_required, q, q1, read_table_file)

bp = Blueprint("academic", __name__)
PER_PAGE = 50
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def class_options():
    return q(f"SELECT c.id, {CLASS_LABEL} AS label FROM classes c ORDER BY c.name, c.stream")


def teacher_options():
    return q("SELECT id, full_name, tsc_no FROM users WHERE active=1 AND role IN ('teacher','deputy_head_teacher','head_teacher','exam_officer','admin') "
             "ORDER BY full_name")


# ---- students ------------------------------------------------------------
@bp.route("/students")
@perm_required("students.view", "students.manage")
def students():
    cls = request.args.get("class_id", type=int)
    status = request.args.get("status", "active")
    text = request.args.get("q", "").strip()
    page = max(request.args.get("page", 1, type=int), 1)
    where, args = [], []
    if cls:
        where.append("s.class_id=?")
        args.append(cls)
    if status in ("active", "left"):
        where.append("s.status=?")
        args.append(status)
    if text:
        where.append("(s.full_name LIKE ? OR s.adm_no LIKE ? OR s.uli LIKE ? OR s.assessment_no LIKE ?)")
        args += [f"%{text}%", f"%{text}%", f"%{text}%", f"%{text}%"]
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = q1(f"SELECT COUNT(*) n FROM students s {w}", args)["n"]
    rows = q(f"SELECT s.*, {CLASS_LABEL} AS class_label FROM students s "
             f"LEFT JOIN classes c ON c.id=s.class_id {w} "
             "ORDER BY c.name, c.stream, s.full_name LIMIT ? OFFSET ?", args + [PER_PAGE, (page - 1) * PER_PAGE])
    return render_template("students.html", students=rows, classes=class_options(), total=total,
                           page=page, pages=max(math.ceil(total / PER_PAGE), 1),
                           f={"class_id": cls, "status": status, "q": text})


def _student_values(f):
    return (f.get("adm_no", "").strip(), f.get("uli", "").strip(), f.get("assessment_no", "").strip(),
            f.get("full_name", "").strip(), f.get("gender", ""),
            f.get("dob", "").strip(), f.get("class_id", type=int) or None,
            f.get("guardian", "").strip(), f.get("phone", "").strip(), f.get("status", "active"))


@bp.route("/students/new", methods=["GET", "POST"])
@bp.route("/students/<int:sid>/edit", methods=["GET", "POST"])
@perm_required("students.manage")
def student_form(sid=None):
    st = q1("SELECT * FROM students WHERE id=?", (sid,)) if sid else None
    if sid and not st:
        abort(404)
    if request.method == "POST":
        adm, uli, assessment_no, name, gender, dob, cid, guardian, phone, status = _student_values(request.form)
        errors = []
        if not adm or not name:
            errors.append("Admission number and full name are required.")
        if q1("SELECT id FROM students WHERE adm_no=? AND id<>?", (adm, sid or 0)):
            errors.append(f"Admission number {adm} already belongs to another student.")
        if errors:
            flash_errors(errors)
            return render_template("student_form.html", st=request.form, sid=sid, classes=class_options())
        if st:
            ex("UPDATE students SET adm_no=?, uli=?, assessment_no=?, full_name=?, gender=?, dob=?, class_id=?, guardian=?, phone=?, status=? WHERE id=?",
               (adm, uli, assessment_no, name, gender, dob, cid, guardian, phone, status, sid))
            audit("student edited", f"{adm} {name}")
            flash("Student updated.", "ok")
        else:
            ex("INSERT INTO students(adm_no,uli,assessment_no,full_name,gender,dob,class_id,guardian,phone,status) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (adm, uli, assessment_no, name, gender, dob, cid, guardian, phone, status))
            audit("student added", f"{adm} {name}")
            flash("Student added.", "ok")
        commit()
        if request.form.get("add_another") and not st:
            return redirect(url_for("academic.student_form", class_id=cid))
        return redirect(url_for("academic.students", class_id=cid or ""))
    default = st or {"status": "active", "class_id": request.args.get("class_id", type=int)}
    return render_template("student_form.html", st=default, sid=sid, classes=class_options())


@bp.route("/students/<int:sid>/delete", methods=["POST"])
@perm_required("students.manage")
def student_delete(sid):
    st = q1("SELECT * FROM students WHERE id=?", (sid,))
    if not st:
        abort(404)
    has_marks = q1("SELECT 1 FROM marks WHERE student_id=? LIMIT 1", (sid,))
    if has_marks and not is_admin():
        flash("This student has exam marks, so only an administrator can delete them. Alternatively, set their status to Left.", "error")
        return redirect(url_for("academic.student_form", sid=sid))
    ex("DELETE FROM marks WHERE student_id=?", (sid,))
    ex("DELETE FROM report_remarks WHERE student_id=?", (sid,))
    ex("DELETE FROM students WHERE id=?", (sid,))
    audit("student deleted", f"{st['adm_no']} {st['full_name']}")
    commit()
    flash(f"Student '{st['full_name']}' deleted.", "ok")
    return redirect(url_for("academic.students"))


@bp.route("/students/export")
@perm_required("students.view", "students.manage")
def students_export():
    data = reports.students_workbook(db(), request.args.get("class_id", type=int))
    return send_file(data, as_attachment=True, download_name="students.xlsx", mimetype=XLSX)


@bp.route("/students/template")
@perm_required("students.manage")
def students_template():
    return send_file(reports.students_template(), as_attachment=True,
                     download_name="students_template.xlsx", mimetype=XLSX)


@bp.route("/students/template.csv")
@perm_required("students.manage")
def students_template_csv():
    return send_file(reports.students_template_csv(), as_attachment=True,
                     download_name="students_template.csv", mimetype="text/csv")


ALIASES = {
    "adm_no": ("admission no", "admission number", "adm no", "adm", "admno", "admission"),
    "uli": ("unique learner identifier (uli)", "unique learner identifier", "uli", "learner identifier", "nembi", "nemis", "upi"),
    "assessment_no": ("assessment no.", "assessment no", "assessment number", "assessment_no", "cba index", "index no", "index number", "cba assessment no", "assessment"),
    "full_name": ("full name", "name", "student name", "student"),
    "gender": ("gender", "sex"),
    "class": ("grade", "class", "form", "level"),
    "stream": ("stream",),
    "dob": ("date of birth", "dob", "birth date", "birthday"),
    "guardian": ("guardian", "parent", "parent/guardian", "guardian name"),
    "phone": ("phone", "telephone", "mobile", "phone number", "contact"),
    "status": ("status",),
}


@bp.route("/students/import", methods=["GET", "POST"])
@perm_required("students.manage")
def students_import():
    report = None
    if request.method == "POST":
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Choose a spreadsheet or CSV file to upload.", "error")
            return render_template("students_import.html", report=None)
        fn_lower = file.filename.lower()
        if not fn_lower.endswith((".xlsx", ".xlsm", ".csv", ".tsv", ".txt")):
            flash("Unsupported file format. Please upload an Excel (.xlsx) or CSV (.csv) file.", "error")
            return render_template("students_import.html", report=None)
        update = bool(request.form.get("update"))
        make_classes = bool(request.form.get("make_classes"))
        try:
            rows = read_table_file(file)
        except Exception as e:
            flash(f"That file could not be read: {e}", "error")
            return render_template("students_import.html", report=None)
        if not rows:
            flash("The uploaded file appears to be empty.", "error")
            return render_template("students_import.html", report=None)
        col, header_idx = {}, None
        for i, row in enumerate(rows[:10]):
            names = [norm_cell(c).lower() for c in row]
            found = {}
            for key, al in ALIASES.items():
                for j, n in enumerate(names):
                    if n in al and key not in found:
                        found[key] = j
            if "adm_no" in found and "full_name" in found:
                col, header_idx = found, i
                break
        if header_idx is None:
            flash("Could not find required columns ('Admission No' and 'Full Name'). Please check column headers or use the template.", "error")
            return render_template("students_import.html", report=None)
        classes = {(r["name"].lower(), r["stream"].lower()): r["id"] for r in q("SELECT id, name, stream FROM classes")}
        added = updated = skipped = 0
        problems = []

        def get(row, key):
            return norm_cell(row[col[key]]) if key in col and col[key] < len(row) else ""

        def norm_date(d_str):
            if not d_str:
                return ""
            d = str(d_str).strip()
            parts = d.replace("/", "-").split("-")
            if len(parts) == 3:
                if len(parts[0]) == 4:
                    return d
                if len(parts[2]) == 4:
                    return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
            return d

        for n, row in enumerate(rows[header_idx + 1:], header_idx + 2):
            if not any(norm_cell(c) for c in row):
                continue
            adm, name = get(row, "adm_no"), get(row, "full_name")
            if not adm or not name:
                problems.append(f"Row {n}: admission number or full name is missing.")
                skipped += 1
                continue
            uli = get(row, "uli")
            assessment_no = get(row, "assessment_no")
            cname, cstream = get(row, "class"), get(row, "stream")
            cid = None
            if cname:
                cid = classes.get((cname.lower(), cstream.lower()))
                if cid is None and make_classes:
                    ex("INSERT INTO classes(name, stream) VALUES(?,?)", (cname, cstream))
                    cid = q1("SELECT last_insert_rowid() id")["id"]
                    classes[(cname.lower(), cstream.lower())] = cid
                elif cid is None:
                    problems.append(f"Row {n}: the class \"{(cname + ' ' + cstream).strip()}\" has not been created yet.")
                    skipped += 1
                    continue
            gender = get(row, "gender")[:1].upper()
            gender = gender if gender in ("M", "F") else ""
            dob = norm_date(get(row, "dob"))
            status = "left" if get(row, "status").lower() == "left" else "active"
            existing = q1("SELECT id FROM students WHERE adm_no=?", (adm,))
            vals = (name, gender, dob, cid, get(row, "guardian"), get(row, "phone"), status, uli, assessment_no)
            if existing:
                if update:
                    ex("UPDATE students SET full_name=?, gender=?, dob=?, class_id=?, guardian=?, phone=?, status=?, uli=?, assessment_no=? WHERE id=?",
                       vals + (existing["id"],))
                    updated += 1
                else:
                    skipped += 1
                    problems.append(f"Row {n}: admission number {adm} already exists (skipped).")
            else:
                ex("INSERT INTO students(full_name,gender,dob,class_id,guardian,phone,status,uli,assessment_no,adm_no) VALUES(?,?,?,?,?,?,?,?,?,?)",
                   vals + (adm,))
                added += 1
        audit("students imported", f"{added} added, {updated} updated, {skipped} skipped")
        commit()
        report = {"added": added, "updated": updated, "skipped": skipped, "problems": problems[:60],
                  "more": max(len(problems) - 60, 0)}
    return render_template("students_import.html", report=report)


# ---- classes ---------------------------------------------------------------
@bp.route("/classes", methods=["GET", "POST"])
@perm_required("classes.manage")
def classes():
    if request.method == "POST":
        name, stream = request.form.get("name", "").strip(), request.form.get("stream", "").strip()
        teacher = request.form.get("class_teacher_id", type=int) or None
        if not name:
            flash("Enter the class name, for example Form 1 or Grade 7.", "error")
        elif q1("SELECT id FROM classes WHERE name=? AND stream=?", (name, stream)):
            flash("That class and stream already exists.", "error")
        else:
            ex("INSERT INTO classes(name, stream, class_teacher_id) VALUES(?,?,?)", (name, stream, teacher))
            audit("class added", f"{name} {stream}")
            commit()
            flash("Class added.", "ok")
        return redirect(url_for("academic.classes"))
    rows = q(f"SELECT c.*, {CLASS_LABEL} AS label, u.full_name AS teacher, u.tsc_no AS teacher_tsc, "
             "(SELECT COUNT(*) FROM students s WHERE s.class_id=c.id AND s.status='active') AS n_students "
             "FROM classes c LEFT JOIN users u ON u.id=c.class_teacher_id ORDER BY c.name, c.stream")
    return render_template("classes.html", classes=rows, teachers=teacher_options())


@bp.route("/classes/<int:cid>/edit", methods=["POST"])
@perm_required("classes.manage")
def class_edit(cid):
    name, stream = request.form.get("name", "").strip(), request.form.get("stream", "").strip()
    teacher = request.form.get("class_teacher_id", type=int) or None
    if not name:
        flash("The class name cannot be empty.", "error")
    elif q1("SELECT id FROM classes WHERE name=? AND stream=? AND id<>?", (name, stream, cid)):
        flash("Another class already has that name and stream.", "error")
    else:
        ex("UPDATE classes SET name=?, stream=?, class_teacher_id=? WHERE id=?", (name, stream, teacher, cid))
        audit("class edited", f"{name} {stream}")
        commit()
        flash("Class updated.", "ok")
    return redirect(url_for("academic.classes"))


@bp.route("/classes/<int:cid>/delete", methods=["POST"])
@perm_required("classes.manage")
def class_delete(cid):
    c = q1("SELECT * FROM classes WHERE id=?", (cid,))
    if not c:
        abort(404)
    has_records = q1("SELECT 1 FROM students WHERE class_id=? LIMIT 1", (cid,)) or \
                  q1("SELECT 1 FROM marks WHERE class_id=? LIMIT 1", (cid,))
    if has_records and not is_admin():
        flash("This class still has students or exam marks, so only an administrator can delete it.", "error")
        return redirect(url_for("academic.classes"))
    if has_records and is_admin():
        ex("UPDATE students SET class_id=NULL WHERE class_id=?", (cid,))
        ex("DELETE FROM marks WHERE class_id=?", (cid,))
        ex("DELETE FROM exam_papers WHERE class_id=?", (cid,))
        ex("DELETE FROM teaching WHERE class_id=?", (cid,))
    ex("DELETE FROM classes WHERE id=?", (cid,))
    audit("class deleted", f"{c['name']} {c['stream']}")
    commit()
    flash("Class deleted.", "ok")
    return redirect(url_for("academic.classes"))


@bp.route("/classes/move", methods=["POST"])
@perm_required("classes.manage")
def classes_move():
    src, dst = request.form.get("from_id", type=int), request.form.get("to_id", type=int)
    if not src or not dst or src == dst:
        flash("Choose two different classes.", "error")
    else:
        n = ex("UPDATE students SET class_id=? WHERE class_id=? AND status='active'", (dst, src)).rowcount
        audit("students moved", f"{n} students moved from class {src} to class {dst}")
        commit()
        flash(f"{n} students moved. Marks from earlier exams stay with the old class.", "ok")
    return redirect(url_for("academic.classes"))


# ---- subjects ----------------------------------------------------------------
@bp.route("/subjects", methods=["GET", "POST"])
@perm_required("subjects.manage")
def subjects():
    if request.method == "POST":
        code = request.form.get("code", "").strip().upper().replace(" ", "")
        name = request.form.get("name", "").strip()
        sid = request.form.get("id", type=int)
        if not code or not name:
            flash("Enter both a short code (for example MATH) and the subject name.", "error")
        elif q1("SELECT id FROM subjects WHERE code=? AND id<>?", (code, sid or 0)):
            flash("That code is already used by another subject.", "error")
        elif sid:
            ex("UPDATE subjects SET code=?, name=? WHERE id=?", (code, name, sid))
            audit("subject edited", f"{code} {name}")
            commit()
            flash("Subject updated.", "ok")
        else:
            ex("INSERT INTO subjects(code, name) VALUES(?,?)", (code, name))
            audit("subject added", f"{code} {name}")
            commit()
            flash("Subject added.", "ok")
        return redirect(url_for("academic.subjects"))
    rows = q("SELECT s.*, (SELECT COUNT(*) FROM exam_papers p WHERE p.subject_id=s.id) AS n_papers "
             "FROM subjects s ORDER BY s.name")
    return render_template("subjects.html", subjects=rows)


@bp.route("/subjects/<int:sid>/delete", methods=["POST"])
@perm_required("subjects.manage")
def subject_delete(sid):
    s = q1("SELECT * FROM subjects WHERE id=?", (sid,))
    if not s:
        abort(404)
    has_records = q1("SELECT 1 FROM exam_papers WHERE subject_id=? LIMIT 1", (sid,)) or \
                  q1("SELECT 1 FROM marks WHERE subject_id=? LIMIT 1", (sid,))
    if has_records and not is_admin():
        flash("This subject is used in examinations, so only an administrator can delete it.", "error")
        return redirect(url_for("academic.subjects"))
    if has_records and is_admin():
        ex("DELETE FROM marks WHERE subject_id=?", (sid,))
        ex("DELETE FROM exam_papers WHERE subject_id=?", (sid,))
        ex("DELETE FROM teaching WHERE subject_id=?", (sid,))
    ex("DELETE FROM subjects WHERE id=?", (sid,))
    audit("subject deleted", f"{s['code']} {s['name']}")
    commit()
    flash("Subject deleted.", "ok")
    return redirect(url_for("academic.subjects"))


# ---- teaching assignments -----------------------------------------------------
@bp.route("/teaching", methods=["GET", "POST"])
@perm_required("subjects.manage")
def teaching():
    cid = request.values.get("class_id", type=int)
    classes_ = class_options()
    if not cid and classes_:
        cid = classes_[0]["id"]
    if request.method == "POST" and cid:
        for s in q("SELECT id FROM subjects"):
            tid = request.form.get(f"t_{s['id']}", type=int)
            ex("DELETE FROM teaching WHERE class_id=? AND subject_id=?", (cid, s["id"]))
            if tid:
                ex("INSERT INTO teaching(teacher_id, class_id, subject_id) VALUES(?,?,?)", (tid, cid, s["id"]))
        audit("teaching assignments saved", f"class {cid}")
        commit()
        flash("Assignments saved.", "ok")
        return redirect(url_for("academic.teaching", class_id=cid))
    subs = q("SELECT s.*, t.teacher_id FROM subjects s LEFT JOIN teaching t "
             "ON t.subject_id=s.id AND t.class_id=? ORDER BY s.name", (cid or 0,))
    by_teacher = q(f"SELECT u.full_name, u.tsc_no, {CLASS_LABEL} AS clabel, s.name AS sname FROM teaching t "
                   "JOIN users u ON u.id=t.teacher_id JOIN classes c ON c.id=t.class_id "
                   "JOIN subjects s ON s.id=t.subject_id ORDER BY u.full_name, c.name, c.stream, s.name")
    return render_template("teaching.html", classes=classes_, class_id=cid, subs=subs,
                           teachers=teacher_options(), by_teacher=by_teacher)
