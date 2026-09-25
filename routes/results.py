"""Class and subject results, student history, and report cards."""
import re
from collections import Counter

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, send_file, url_for)
from markupsafe import Markup, escape

import calc
import reports
import os
from core import (CLASS_LABEL, LOGO_PATH, audit, can_print_reports, can_view_class, commit, db, ex, fnum,
                  has_perm, login_required, printable_class_ids, q, q1, settings, teacher_papers,
                  visible_class_ids, f1)

bp = Blueprint("results", __name__)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF = "application/pdf"


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def exam_choices():
    return q("SELECT * FROM exams ORDER BY year DESC, id DESC")


def pick_exam():
    exams = exam_choices()
    eid = request.args.get("exam_id", type=int)
    if not eid and exams:
        eid = exams[0]["id"]
    return exams, eid


def visible_classes(eid=None, printable=False):
    """Classes (id,label,name) the user may see, limited to classes in the exam if given."""
    ids = printable_class_ids() if printable else visible_class_ids()
    rows = q(f"SELECT c.id, c.name, {CLASS_LABEL} AS label FROM classes c ORDER BY c.name, c.stream")
    if eid:
        in_exam = {r["class_id"] for r in q("SELECT DISTINCT class_id FROM exam_papers WHERE exam_id=?", (eid,))}
        rows = [r for r in rows if r["id"] in in_exam]
    return [r for r in rows if ids is None or r["id"] in ids]


def scope_options(classes, all_visible):
    opts = [{"value": f"c{c['id']}", "label": c["label"]} for c in classes]
    if all_visible:
        levels = Counter(c["name"] for c in classes)
        for name, n in levels.items():
            if n > 1:
                opts.append({"value": f"l{name}", "label": f"{name} - all streams"})
    return opts


def resolve_scope(scope, classes):
    """-> (class_ids, label) or (None, None) if not allowed / empty."""
    by_id = {c["id"]: c for c in classes}
    if scope and scope.startswith("c") and scope[1:].isdigit() and int(scope[1:]) in by_id:
        c = by_id[int(scope[1:])]
        return [c["id"]], c["label"]
    if scope and scope.startswith("l"):
        ids = [c["id"] for c in classes if c["name"] == scope[1:]]
        if ids:
            return ids, f"{scope[1:]} (all streams)"
    return None, None


# ---------------------------------------------------------------------------
# class results
# ---------------------------------------------------------------------------
@bp.route("/results")
@login_required
def results_home():
    return redirect(url_for("results.class_results", **request.args))


@bp.route("/results/class")
@login_required
def class_results():
    exams, eid = pick_exam()
    classes = visible_classes(eid)
    all_visible = visible_class_ids() is None
    options = scope_options(classes, all_visible)
    scope = request.args.get("scope") or (options[0]["value"] if options else "")
    ids, label = resolve_scope(scope, classes)
    res = mt = None
    dist = []
    tiles = {}
    if eid and ids:
        res = calc.compute_results(db(), eid)
        mt = calc.merit_table(res, ids)
        ranked = [s for s in res["students"].values() if s["class_id"] in ids and s["n"]]
        cnt = Counter(s["grade"] for s in ranked)
        dist = [(b["grade"], cnt.get(b["grade"], 0)) for b in res["bands"]]
        if ranked:
            tiles = {"n": len(ranked), "mean": sum(s["avg_pct"] for s in ranked) / len(ranked),
                     "points": sum(s["mean_points"] for s in ranked) / len(ranked),
                     "top": max(ranked, key=lambda s: s["avg_pct"])}
    return render_template("results_class.html", exams=exams, eid=eid, options=options, scope=scope,
                           label=label, mt=mt, res=res, dist=dist, tiles=tiles)


@bp.route("/results/class/download")
@login_required
def class_results_download():
    eid = request.args.get("exam_id", type=int)
    classes = visible_classes(eid)
    ids, label = resolve_scope(request.args.get("scope"), classes)
    if not eid or not ids:
        abort(404)
    if len(ids) > 1 and visible_class_ids() is not None:
        abort(403)
    res = calc.compute_results(db(), eid)
    mt = calc.merit_table(res, ids)
    title = f"Class results: {label}"
    name = _slug(f"{res['exam']['name']}_{label}")
    if request.args.get("fmt") == "pdf":
        return send_file(reports.merit_pdf(settings()["school_name"], res, mt, title), as_attachment=True,
                         download_name=f"results_{name}.pdf", mimetype=PDF)
    return send_file(reports.merit_xlsx(res, mt, title), as_attachment=True,
                     download_name=f"results_{name}.xlsx", mimetype=XLSX)


# ---------------------------------------------------------------------------
# subject results
# ---------------------------------------------------------------------------
def _subject_rows(res, class_id):
    allowed = None
    if not has_perm("results.view_all"):
        allowed = teacher_papers()
    return calc.subject_table(res, class_id or None, allowed)


@bp.route("/results/subjects")
@login_required
def subject_results():
    exams, eid = pick_exam()
    class_id = request.args.get("class_id", type=int)
    res = rows = None
    classes = visible_classes(eid)
    if class_id and class_id not in {c["id"] for c in classes}:
        class_id = None
    if eid:
        res = calc.compute_results(db(), eid)
        rows = _subject_rows(res, class_id)
        if not has_perm("results.view_all"):
            mine = {cid for cid, _ in teacher_papers()}
            classes = [c for c in classes if c["id"] in mine]
    return render_template("results_subjects.html", exams=exams, eid=eid, classes=classes,
                           class_id=class_id, rows=rows, res=res)


@bp.route("/results/subjects/download")
@login_required
def subject_results_download():
    eid = request.args.get("exam_id", type=int)
    class_id = request.args.get("class_id", type=int)
    if not eid:
        abort(404)
    res = calc.compute_results(db(), eid)
    rows = _subject_rows(res, class_id)
    title = "Subject results"
    name = _slug(res["exam"]["name"])
    if request.args.get("fmt") == "pdf":
        return send_file(reports.subjects_pdf(settings()["school_name"], res, rows, title), as_attachment=True,
                         download_name=f"subjects_{name}.pdf", mimetype=PDF)
    return send_file(reports.subjects_xlsx(res, rows, title), as_attachment=True,
                     download_name=f"subjects_{name}.xlsx", mimetype=XLSX)


# ---------------------------------------------------------------------------
# student history
# ---------------------------------------------------------------------------
def trend_svg(points, pass_mark):
    """points: [(label, value 0-100)] -> inline SVG."""
    if not points:
        return ""
    W, H, L, R, T, B = 640, 240, 40, 16, 16, 52
    iw, ih = W - L - R, H - T - B
    n = len(points)
    xs = [L + (iw * (i + 0.5) / n) for i in range(n)]
    y = lambda v: T + ih * (1 - v / 100)
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Average percentage by examination" class="trend">']
    for g_ in (0, 25, 50, 75, 100):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(g_):.1f}" y2="{y(g_):.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{y(g_) + 3:.1f}" text-anchor="end" class="axis">{g_}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(pass_mark):.1f}" y2="{y(pass_mark):.1f}" class="passline"/>')
    if n > 1:
        pts = " ".join(f"{x:.1f},{y(v):.1f}" for x, (_, v) in zip(xs, points))
        out.append(f'<polyline points="{pts}" class="line"/>')
    for x, (lab, v) in zip(xs, points):
        out.append(f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="4" class="dot"/>'
                   f'<text x="{x:.1f}" y="{y(v) - 9:.1f}" text-anchor="middle" class="val">{v:.1f}</text>'
                   f'<text x="{x:.1f}" y="{H - 30}" text-anchor="middle" class="axis">{escape(lab[:16])}</text>')
    out.append("</svg>")
    return Markup("".join(out))


@bp.route("/students/<int:sid>/history")
@login_required
def student_history(sid):
    st = q1(f"SELECT s.*, {CLASS_LABEL} AS class_label FROM students s LEFT JOIN classes c ON c.id=s.class_id WHERE s.id=?", (sid,))
    if not st:
        abort(404)
    if not (has_perm("students.view") or has_perm("results.view_all") or (st["class_id"] and can_view_class(st["class_id"]))):
        abort(403)
    hist = calc.student_history(db(), sid)
    if request.args.get("fmt") in ("xlsx", "pdf"):
        if request.args["fmt"] == "pdf":
            data = reports.history_pdf(settings()["school_name"], st, hist, st["class_label"] or "")
            return send_file(data, as_attachment=True, download_name=f"history_{_slug(st['adm_no'])}.pdf", mimetype=PDF)
        data = reports.history_xlsx(st, hist, st["class_label"] or "")
        return send_file(data, as_attachment=True, download_name=f"history_{_slug(st['adm_no'])}.xlsx", mimetype=XLSX)
    pass_mark = float(settings().get("pass_mark") or 40)
    chart = trend_svg([(f"{h['exam']['name']}", h["student"]["avg_pct"]) for h in hist], pass_mark)
    # subject x exam matrix of percentages
    subj = {}
    for h in hist:
        for sub_id, cell in h["student"]["subjects"].items():
            subj.setdefault(sub_id, {"name": h["res"]["subjects"][sub_id]["name"], "vals": {}})
            subj[sub_id]["vals"][h["exam"]["id"]] = cell
    can_print = bool(st["class_id"]) and can_print_reports(st["class_id"])
    return render_template("student_history.html", st=st, hist=hist, chart=chart, subj=sorted(subj.values(), key=lambda x: x["name"]),
                           pass_mark=pass_mark, can_print=can_print)


# ---------------------------------------------------------------------------
# report cards
# ---------------------------------------------------------------------------
def _remarks(eid, class_id=None):
    rows = q("SELECT r.* FROM report_remarks r JOIN students s ON s.id=r.student_id WHERE r.exam_id=?"
             + (" AND s.class_id=?" if class_id else ""), (eid, class_id) if class_id else (eid,))
    return {r["student_id"]: dict(r) for r in rows}


def _class_contexts(res, cid, remarks, only=None):
    students = [s for s in res["students"].values() if s["class_id"] == cid]
    students.sort(key=lambda s: (s["pos_stream"] is None, s["pos_stream"] or 0, s["name"]))
    out = []
    for s in students:
        if only and s["id"] != only:
            continue
        ctx = calc.report_context(res, s["id"], remarks.get(s["id"]))
        if ctx:
            out.append(ctx)
    return out


@bp.route("/reports")
@login_required
def reports_home():
    exams, eid = pick_exam()
    classes = visible_classes(eid, printable=True)
    cid = request.args.get("class_id", type=int) or (classes[0]["id"] if classes else None)
    if cid and cid not in {c["id"] for c in classes}:
        cid = classes[0]["id"] if classes else None
    rows, unverified, missing = [], 0, 0
    res = None
    if eid and cid:
        res = calc.compute_results(db(), eid)
        rows = sorted([s for s in res["students"].values() if s["class_id"] == cid],
                      key=lambda s: (s["pos_stream"] is None, s["pos_stream"] or 0, s["name"]))
        unverified = q1("SELECT COUNT(*) n FROM marks WHERE exam_id=? AND class_id=? AND status<>'verified'", (eid, cid))["n"]
        listed = {s["id"] for s in rows}
        missing = q1("SELECT COUNT(*) n FROM students WHERE class_id=? AND status='active' AND id NOT IN (%s)"
                     % (",".join(str(i) for i in listed) or "0"), (cid,))["n"]
    return render_template("reports.html", exams=exams, eid=eid, classes=classes, cid=cid, rows=rows,
                           unverified=unverified, missing=missing, res=res)


def _check_report_access(eid, cid):
    if not can_print_reports(cid):
        abort(403)
    if not q1("SELECT 1 FROM exam_papers WHERE exam_id=? AND class_id=? LIMIT 1", (eid, cid)):
        abort(404)


@bp.route("/reports/card/<int:eid>/<int:sid>")
@login_required
def report_card(eid, sid):
    st = q1("SELECT * FROM students WHERE id=?", (sid,))
    if not st:
        abort(404)
    res = calc.compute_results(db(), eid)
    s = res["students"].get(sid)
    if not s:
        flash("This student has no results in this examination yet.", "error")
        return redirect(url_for("results.reports_home", exam_id=eid, class_id=st["class_id"]))
    _check_report_access(eid, s["class_id"])
    ctx = calc.report_context(res, sid, _remarks(eid, s["class_id"]).get(sid))
    return render_template("report_print.html", contexts=[ctx], st=settings(),
                          has_logo=os.path.exists(LOGO_PATH), title=f"Report card - {s['name']}")


@bp.route("/reports/print")
@login_required
def report_print():
    eid, cid = request.args.get("exam_id", type=int), request.args.get("class_id", type=int)
    if not eid or not cid:
        abort(404)
    _check_report_access(eid, cid)
    res = calc.compute_results(db(), eid)
    ctxs = _class_contexts(res, cid, _remarks(eid, cid))
    if not ctxs:
        flash("No results to print for this class yet.", "error")
        return redirect(url_for("results.reports_home", exam_id=eid, class_id=cid))
    audit("report cards printed", f"{res['exam']['name']}: {len(ctxs)} cards, class {cid}")
    commit()
    return render_template("report_print.html", contexts=ctxs, st=settings(),
                          has_logo=os.path.exists(LOGO_PATH), title=f"Report cards - {res['classes'][cid]['label']}")


@bp.route("/reports/pdf")
@login_required
def report_pdf():
    eid, cid = request.args.get("exam_id", type=int), request.args.get("class_id", type=int)
    sid = request.args.get("student_id", type=int)
    if not eid or not cid:
        abort(404)
    _check_report_access(eid, cid)
    res = calc.compute_results(db(), eid)
    ctxs = _class_contexts(res, cid, _remarks(eid, cid), only=sid)
    if not ctxs:
        abort(404)
    label = res["classes"][cid]["label"]
    name = _slug(ctxs[0]["student"]["name"]) if sid else _slug(f"{res['exam']['name']}_{label}")
    audit("report cards downloaded", f"{res['exam']['name']}: {len(ctxs)} cards, class {cid}")
    commit()
    return send_file(reports.report_cards_pdf(ctxs, settings()), as_attachment=True,
                     download_name=f"report_cards_{name}.pdf", mimetype=PDF)


@bp.route("/reports/remarks", methods=["GET", "POST"])
@login_required
def report_remarks():
    eid = request.values.get("exam_id", type=int)
    cid = request.values.get("class_id", type=int)
    if not eid or not cid:
        abort(404)
    _check_report_access(eid, cid)
    exam = q1("SELECT * FROM exams WHERE id=?", (eid,))
    label = q1(f"SELECT {CLASS_LABEL} AS l FROM classes c WHERE c.id=?", (cid,))["l"]
    res = calc.compute_results(db(), eid)
    students = sorted([s for s in res["students"].values() if s["class_id"] == cid],
                      key=lambda s: (s["pos_stream"] is None, s["pos_stream"] or 0, s["name"]))
    remarks = _remarks(eid, cid)
    principal_ok = has_perm("reports.print_all")
    if request.method == "POST":
        action = request.form.get("action", "save")
        for s in students:
            cur = remarks.get(s["id"], {"class_teacher": "", "principal": ""})
            ct = request.form.get(f"ct_{s['id']}", cur["class_teacher"]).strip()
            pr = request.form.get(f"pr_{s['id']}", cur["principal"]).strip() if principal_ok else cur["principal"]
            if action == "suggest_teacher" and not ct:
                ct = s["comment"]
            if action == "suggest_principal" and principal_ok and not pr:
                pr = s["comment"]
            ex("INSERT INTO report_remarks(exam_id,student_id,class_teacher,principal) VALUES(?,?,?,?) "
               "ON CONFLICT(exam_id,student_id) DO UPDATE SET class_teacher=excluded.class_teacher, principal=excluded.principal",
               (eid, s["id"], ct, pr))
        audit("remarks saved", f"{exam['name']}: {label}")
        commit()
        flash("Remarks saved." if action == "save" else "Suggested remarks filled in where the box was empty. Edit them as you wish.", "ok")
        return redirect(url_for("results.report_remarks", exam_id=eid, class_id=cid))
    return render_template("remarks.html", exam=exam, label=label, cid=cid, students=students,
                           remarks=remarks, principal_ok=principal_ok)
