"""All result calculations: grades, totals, averages, positions and statistics.

compute_results() is the single source of truth. The screens, the Excel files and
the PDF files all read from what it returns, so they can never disagree.
"""
from collections import defaultdict

from core import CLASS_LABEL


def get_bands(con):
    return [dict(r) for r in con.execute("SELECT * FROM grade_bands ORDER BY min_pct DESC")]


def band_for(pct, bands):
    if pct is None or not bands:
        return None
    for b in bands:
        if pct + 1e-9 >= b["min_pct"]:
            return b
    return bands[-1]


def rank_map(items):
    """items: [(key, value)] -> {key: rank}. Equal values share a rank (1,2,2,4)."""
    ordered = sorted(items, key=lambda kv: -kv[1])
    ranks, prev, rank = {}, None, 0
    for i, (k, v) in enumerate(ordered, 1):
        if v != prev:
            rank, prev = i, v
        ranks[k] = rank
    return ranks


def _setting(con, key, default):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r and r["value"] != "" else default


def compute_results(con, exam_id, only_verified=None):
    exam = con.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
    if not exam:
        return None
    if only_verified is None:
        only_verified = _setting(con, "require_verified", "0") == "1"
    pass_mark = float(_setting(con, "pass_mark", "40"))
    bands = get_bands(con)

    classes = {r["id"]: dict(r) for r in con.execute(
        f"SELECT c.*, {CLASS_LABEL} AS label FROM classes c")}
    subjects = {r["id"]: dict(r) for r in con.execute("SELECT * FROM subjects ORDER BY name")}
    papers = {(r["class_id"], r["subject_id"]): dict(r)
              for r in con.execute("SELECT * FROM exam_papers WHERE exam_id=?", (exam_id,))}
    teachers = {}
    for r in con.execute("SELECT t.class_id, t.subject_id, u.full_name, u.initials "
                         "FROM teaching t JOIN users u ON u.id=t.teacher_id"):
        teachers[(r["class_id"], r["subject_id"])] = {
            "name": r["full_name"], "short": r["initials"] or r["full_name"]}
    students = {r["id"]: dict(r) for r in con.execute("SELECT * FROM students")}

    S = {}
    for m in con.execute("SELECT * FROM marks WHERE exam_id=?", (exam_id,)):
        key = (m["class_id"], m["subject_id"])
        paper = papers.get(key)
        st = students.get(m["student_id"])
        if not paper or not st:
            continue
        if only_verified and m["status"] != "verified":
            continue
        s = S.setdefault(m["student_id"], {
            "id": m["student_id"], "adm_no": st["adm_no"], "name": st["full_name"],
            "gender": st["gender"], "class_id": m["class_id"],
            "uli": st.get("uli") or "", "assessment_no": st.get("assessment_no") or "",
            "subjects": {}, "total": 0.0, "possible": 0.0, "n": 0, "points": 0.0})
        out_of = paper["out_of"]
        cell = {"score": None, "out_of": out_of, "pct": None, "grade": "", "points": 0,
                "remark": "", "absent": bool(m["absent"]), "status": m["status"], "pos": None}
        if not m["absent"] and m["score"] is not None:
            pct = m["score"] / out_of * 100 if out_of else 0
            b = band_for(pct, bands)
            cell.update(score=m["score"], pct=pct,
                        grade=b["grade"] if b else "", points=b["points"] if b else 0,
                        remark=b["remark"] if b else "")
            s["total"] += m["score"]
            s["possible"] += out_of
            s["points"] += cell["points"]
            s["n"] += 1
        s["subjects"][m["subject_id"]] = cell

    # overall figures per student
    for s in S.values():
        s["avg_pct"] = None
        s["grade"], s["remark"], s["comment"], s["mean_points"] = "", "", "", None
        if s["n"]:
            s["avg_pct"] = s["total"] / s["possible"] * 100 if s["possible"] else 0
            b = band_for(s["avg_pct"], bands)
            if b:
                s["grade"], s["remark"], s["comment"] = b["grade"], b["remark"], b["comment"]
            s["mean_points"] = s["points"] / s["n"]

    # positions: in the stream (class record) and across all streams of the same class name
    by_stream, by_level = defaultdict(list), defaultdict(list)
    for sid, s in S.items():
        if s["n"]:
            v = round(s["avg_pct"], 4)
            by_stream[s["class_id"]].append((sid, v))
            by_level[classes[s["class_id"]]["name"]].append((sid, v))
    for items in by_stream.values():
        for sid, r in rank_map(items).items():
            S[sid]["pos_stream"], S[sid]["n_stream"] = r, len(items)
    for items in by_level.values():
        for sid, r in rank_map(items).items():
            S[sid]["pos_class"], S[sid]["n_class"] = r, len(items)
    for s in S.values():
        s.setdefault("pos_stream", None)
        s.setdefault("n_stream", 0)
        s.setdefault("pos_class", None)
        s.setdefault("n_class", 0)

    # subject positions and paper statistics
    per_paper = defaultdict(list)
    for sid, s in S.items():
        for subj_id, cell in s["subjects"].items():
            per_paper[(s["class_id"], subj_id)].append((sid, cell))
    expected = {r["class_id"]: r["n"] for r in con.execute(
        "SELECT class_id, COUNT(*) n FROM students WHERE status='active' GROUP BY class_id")}

    def make_stats(cells):
        scored = [c for c in cells if c["score"] is not None]
        dist = {b["grade"]: 0 for b in bands}
        for c in scored:
            if c["grade"] in dist:
                dist[c["grade"]] += 1
        n = len(scored)
        return {
            "entries": len(cells), "sat": n, "absent": sum(1 for c in cells if c["absent"]),
            "mean_score": sum(c["score"] for c in scored) / n if n else None,
            "mean_pct": sum(c["pct"] for c in scored) / n if n else None,
            "high": max((c["score"] for c in scored), default=None),
            "low": min((c["score"] for c in scored), default=None),
            "pass_rate": sum(1 for c in scored if c["pct"] >= pass_mark) / n * 100 if n else None,
            "dist": dist,
        }

    P = {}
    for key, pairs in per_paper.items():
        rm = rank_map([(sid, round(c["pct"], 4)) for sid, c in pairs if c["pct"] is not None])
        for sid, c in pairs:
            c["pos"] = rm.get(sid)
        P[key] = make_stats([c for _, c in pairs])
        P[key]["expected"] = expected.get(key[0], 0)
    T = {}
    by_subject = defaultdict(list)
    for (cid, subj_id), pairs in per_paper.items():
        by_subject[subj_id].extend(c for _, c in pairs)
    for subj_id, cells in by_subject.items():
        T[subj_id] = make_stats(cells)

    C = {}
    by_class = defaultdict(list)
    for s in S.values():
        if s["n"]:
            by_class[s["class_id"]].append(s)
    for cid, lst in by_class.items():
        C[cid] = {"n": len(lst),
                  "mean_pct": sum(x["avg_pct"] for x in lst) / len(lst),
                  "mean_points": sum(x["mean_points"] for x in lst) / len(lst),
                  "top": max(lst, key=lambda x: x["avg_pct"])}

    return {"exam": dict(exam), "bands": bands, "pass_mark": pass_mark, "classes": classes,
            "subjects": subjects, "papers": papers, "teachers": teachers,
            "students": S, "paper_stats": P, "subject_stats": T, "class_stats": C,
            "only_verified": only_verified}


# ---------------------------------------------------------------------------
# Tables shared by the screens, Excel and PDF
# ---------------------------------------------------------------------------
def class_subject_ids(res, class_ids):
    ids = {sid for (cid, sid) in res["papers"] if cid in class_ids}
    return sorted(ids, key=lambda i: res["subjects"][i]["name"])


def merit_table(res, class_ids):
    """Ranked list of students in the given classes (streams)."""
    class_ids = set(class_ids)
    subj_ids = class_subject_ids(res, class_ids)
    rows_src = [s for s in res["students"].values() if s["class_id"] in class_ids]
    single = len(class_ids) == 1
    for s in rows_src:
        s["_pos"] = s["pos_stream"] if single else s["pos_class"]
    rows_src.sort(key=lambda s: (s["_pos"] is None, s["_pos"] or 0, s["name"]))
    headers = ["Pos", "Adm No", "Name"] + [res["subjects"][i]["code"] for i in subj_ids] + \
              ["Total", "Avg %", "Grade", "Points"]
    if not single:
        headers.insert(3, "Class")
    rows = []
    for s in rows_src:
        row = [s["_pos"] if s["_pos"] is not None else "-", s["adm_no"], s["name"]]
        if not single:
            row.append(res["classes"][s["class_id"]]["label"])
        for i in subj_ids:
            c = s["subjects"].get(i)
            row.append("" if c is None else ("ABS" if c["absent"] else c["score"]))
        row += [s["total"] if s["n"] else "", s["avg_pct"] if s["n"] else "",
                s["grade"], s["mean_points"] if s["n"] else ""]
        rows.append(row)
    # subject means footer
    footer = ["", "", "Mean"] + ([""] if not single else [])
    for i in subj_ids:
        vals = [s["subjects"][i]["score"] for s in rows_src
                if i in s["subjects"] and s["subjects"][i]["score"] is not None]
        footer.append(sum(vals) / len(vals) if vals else "")
    means = [s["avg_pct"] for s in rows_src if s["n"]]
    pts = [s["mean_points"] for s in rows_src if s["n"]]
    tot = [s["total"] for s in rows_src if s["n"]]
    footer += [sum(tot) / len(tot) if tot else "", sum(means) / len(means) if means else "",
               "", sum(pts) / len(pts) if pts else ""]
    return {"headers": headers, "rows": rows, "footer": footer, "subject_ids": subj_ids,
            "single": single}


def subject_table(res, class_id=None, allowed=None):
    """One row per paper (class x subject). allowed = set of (class_id, subject_id) or None."""
    rows = []
    for (cid, sid), st in res["paper_stats"].items():
        if class_id and cid != class_id:
            continue
        if allowed is not None and (cid, sid) not in allowed:
            continue
        t = res["teachers"].get((cid, sid))
        rows.append({"class_id": cid, "subject_id": sid, "class": res["classes"][cid]["label"],
                     "subject": res["subjects"][sid]["name"], "code": res["subjects"][sid]["code"],
                     "teacher": t["name"] if t else "", **st})
    rows.sort(key=lambda r: (r["class"], r["subject"]))
    return rows


def report_context(res, sid, remarks=None):
    s = res["students"].get(sid)
    if not s or not s["n"] and not s["subjects"]:
        return None
    rows = []
    for subj_id in class_subject_ids(res, {s["class_id"]}):
        c = s["subjects"].get(subj_id)
        if c is None:
            continue
        t = res["teachers"].get((s["class_id"], subj_id))
        rows.append({"subject": res["subjects"][subj_id]["name"], "code": res["subjects"][subj_id]["code"],
                     "teacher": t["short"] if t else "", **c})
    cls = res["classes"][s["class_id"]]
    return {"student": s, "class_label": cls["label"], "exam": res["exam"], "rows": rows,
            "bands": res["bands"], "pass_mark": res["pass_mark"],
            "teacher_remark": (remarks or {}).get("class_teacher", ""),
            "principal_remark": (remarks or {}).get("principal", "")}


def student_history(con, student_id):
    """Every exam this student has results for, oldest first."""
    exam_rows = con.execute(
        "SELECT DISTINCT e.id FROM exams e JOIN marks m ON m.exam_id=e.id "
        "WHERE m.student_id=? ORDER BY e.year, e.id", (student_id,)).fetchall()
    out = []
    for er in exam_rows:
        res = compute_results(con, er["id"])
        s = res["students"].get(student_id) if res else None
        if s and s["n"]:
            out.append({"exam": res["exam"], "student": s, "res": res,
                        "class_label": res["classes"][s["class_id"]]["label"]})
    return out
