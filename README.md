# School Examination Management System

A simple, offline examination, marks and report-card system. Runs on your
own computer - no internet connection and no cloud account required. All
data is stored in the `data` folder next to this program.

## Requirements

- Python 3.10 or newer (download from python.org if you don't have it)
- Works on Windows, macOS and Linux

## First-time setup

Open a command prompt / terminal in this folder and run:

```
pip install -r requirements.txt
```

## Running the system

```
python app.py
```

This starts the program and opens it in your web browser automatically at
`http://127.0.0.1:5000`. Leave the black command-prompt window open while you
use the system; closing it stops the program. To use it again later, just
run `python app.py` again.

**First login:** username `admin`, password `admin123`. You will be asked to
choose a new password immediately.

## Where your data lives

Everything is stored in the `data` folder:
- `data/exams.db` - the database (students, marks, results, everything)
- `data/backups/` - backup files you create from Backup & Restore
- `data/logo.png` - your school logo, if you upload one

To move the system to another computer, copy the whole program folder
(including `data`) or restore a backup on the new computer.

**Back this folder up regularly** - for example by copying it to a USB
drive, or setting a backup copy folder in Settings > Backup. There is no
cloud copy; if this computer's disk fails and you have no backup, the data
is gone.

## Everyday use

1. **School settings** (School settings, Grading scale) - set your school
   name, pass mark and grading scale once, before your first exam.
2. **Set up classes, subjects and teachers** - Classes & streams, Subjects,
   Teaching assignments, and bulk upload teachers from Excel/CSV (Users > Upload teachers).
3. **Add students** - one by one, or upload many at once from Excel or CSV
   (Students > Upload Excel / CSV) using the 10-column layout:
   `Admission No`, `Unique Learner Identifier (ULI)`, `ASSESSMENT NO.`, `Full Name`, `Gender`, `Grade`, `Stream`, `Date of Birth`, `Guardian`, `Phone`.
4. **Create an examination** - choose which classes and subjects sit it.
5. **Enter marks** - teachers log in and enter marks for their own subjects,
   or you can upload marks from Excel for a whole class at once.
6. **Verify marks** (optional, for exam officers/admins) - lock marks once
   checked so teachers can no longer change them by accident.
7. **Report cards** - once marks are in, go to Report cards to view, print
   or download report cards for a class, or Class/Subject results for
   summaries. Includes student ULI and Assessment No on report cards and records.

## User roles & Leadership

- **Administrator** - full, unrestricted authority across the entire system. Can delete teachers (via the teacher Edit screen), delete students (even if marks are already recorded, with automatic cascade), delete exam entries and clear marks for specific papers, delete examinations, and configure all system settings and permissions.
- **Head Teacher / Principal** - institutional executive with full academic results oversight, report card remarks authoring, verification, and school audit logging.
- **Deputy Head Teacher** - academic lead managing curriculum, exams setup, class and teaching allocations, marks verification, and progress monitoring.
- **Examination officer** - manages examinations, papers, marks entry schedules, verification, and results printing.
- **Teacher** - enters, edits, and manages marks strictly and ONLY for the students they teach and the subjects/classes they are assigned to. All teachers have registered TSC numbers.
- **Viewer** - read-only access to students and academic results.

An administrator can fine-tune exactly what each role can do under
**Roles & permissions** (`/roles`), and manage teachers and School Leadership on the **Teachers & Users** page (`/users`). Teachers can be deleted directly from their profile edit screen (`/users/<id>/edit`).

## Support

This is a private tool for your school. If something looks wrong, check the
Activity log (System > Activity log) to see what changed and by whom.
