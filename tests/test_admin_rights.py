import os
import sys
import re
import secrets
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app
import core

class AdminRightsTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

        with self.app.app_context():
            core.init_db()
            core.ex("DELETE FROM teaching WHERE teacher_id IN (SELECT id FROM users WHERE username IN ('teacherdel101', 'strictteacher'))")
            core.ex("DELETE FROM users WHERE username IN ('teacherdel101', 'strictteacher')")
            core.commit()
            admin = core.q1("SELECT * FROM users WHERE role='admin' AND active=1")
            self.assertIsNotNone(admin, "Admin user must exist")
            self.admin_id = admin['id']

        # Set session as admin
        with self.client.session_transaction() as sess:
            sess['uid'] = self.admin_id
            sess['csrf'] = 'test_admin_csrf_token_12345'
        self.csrf = 'test_admin_csrf_token_12345'

    def tearDown(self):
        with self.app.app_context():
            core.ex("DELETE FROM teaching WHERE teacher_id IN (SELECT id FROM users WHERE username IN ('teacherdel101', 'strictteacher'))")
            core.ex("DELETE FROM users WHERE username IN ('teacherdel101', 'strictteacher')")
            core.commit()

    def test_teacher_crud_and_delete_on_edit(self):
        # 1. Create a teacher
        res = self.client.post('/users/new', data={
            'csrf_token': self.csrf,
            'full_name': 'Delete Test Teacher',
            'tsc_no': 'TSC-DEL-101',
            'username': 'teacherdel101',
            'role': 'teacher',
            'password': 'password123',
            'active': '1'
        }, follow_redirects=True)
        self.assertIn(b'User created', res.data)

        with self.app.app_context():
            teacher = core.q1("SELECT * FROM users WHERE username='teacherdel101'")
            self.assertIsNotNone(teacher)
            t_id = teacher['id']

            # Assign teacher to a class & subject
            cls = core.q1("SELECT id FROM classes LIMIT 1")
            sub = core.q1("SELECT id FROM subjects LIMIT 1")
            if not cls:
                core.ex("INSERT INTO classes(name, stream) VALUES('Form 1', 'East')")
                cls = core.q1("SELECT last_insert_rowid() id")
            if not sub:
                core.ex("INSERT INTO subjects(code, name) VALUES('ENG', 'English')")
                sub = core.q1("SELECT last_insert_rowid() id")

            core.ex("INSERT OR REPLACE INTO teaching(teacher_id, class_id, subject_id) VALUES(?,?,?)",
                    (t_id, cls['id'], sub['id']))
            core.ex("UPDATE classes SET class_teacher_id=? WHERE id=?", (t_id, cls['id']))
            core.commit()

        # 2. View edit page -> Must have Delete Teacher card & button
        res = self.client.get(f'/users/{t_id}/edit')
        self.assertIn(b'Delete Teacher', res.data)
        self.assertIn(f'/users/{t_id}/delete'.encode(), res.data)

        # 3. Perform Delete teacher POST
        res = self.client.post(f'/users/{t_id}/delete', data={'csrf_token': self.csrf}, follow_redirects=True)
        self.assertIn(b'permanently deleted', res.data)

        # Verify teacher is gone and teaching references cleared
        with self.app.app_context():
            deleted_teacher = core.q1("SELECT id FROM users WHERE id=?", (t_id,))
            self.assertIsNone(deleted_teacher)
            teaching_refs = core.q("SELECT id FROM teaching WHERE teacher_id=?", (t_id,))
            self.assertEqual(len(teaching_refs), 0)
            class_ref = core.q1("SELECT id FROM classes WHERE class_teacher_id=?", (t_id,))
            self.assertIsNone(class_ref)
        print("PASS: Teacher deletion on edit and unassignment cascade verified!")

    def test_admin_delete_student_with_marks(self):
        with self.app.app_context():
            cls = core.q1("SELECT id FROM classes LIMIT 1")
            if not cls:
                core.ex("INSERT INTO classes(name, stream) VALUES('Form 2', 'West')")
                cls = core.q1("SELECT last_insert_rowid() id")
            sub = core.q1("SELECT id FROM subjects LIMIT 1")
            if not sub:
                core.ex("INSERT INTO subjects(code, name) VALUES('KIS', 'Kiswahili')")
                sub = core.q1("SELECT last_insert_rowid() id")
            exam = core.q1("SELECT id FROM exams LIMIT 1")
            if not exam:
                core.ex("INSERT INTO exams(name, term, year, out_of, status) VALUES('Mid Term', 'Term 1', 2026, 100, 'open')")
                exam = core.q1("SELECT last_insert_rowid() id")

            # Create dummy student
            unique_adm = f'ST-DEL-{secrets.token_hex(4)}'
            core.ex("INSERT INTO students(adm_no, full_name, class_id, status) VALUES(?, 'Student For Delete', ?, 'active')",
                    (unique_adm, cls['id']))
            st_id = core.q1("SELECT last_insert_rowid() id")['id']

            # Add mark for student
            core.ex("INSERT OR REPLACE INTO marks(exam_id, student_id, class_id, subject_id, score) VALUES(?,?,?,?,?)",
                    (exam['id'], st_id, cls['id'], sub['id'], 85.0))
            core.commit()

        # Admin deletes student
        res = self.client.post(f'/students/{st_id}/delete', data={'csrf_token': self.csrf}, follow_redirects=True)
        self.assertIn(b'deleted', res.data)

        with self.app.app_context():
            self.assertIsNone(core.q1("SELECT id FROM students WHERE id=?", (st_id,)))
            self.assertIsNone(core.q1("SELECT id FROM marks WHERE student_id=?", (st_id,)))
        print("PASS: Admin delete student with marks verified!")

    def test_admin_delete_and_clear_exam_paper_entry(self):
        with self.app.app_context():
            cls = core.q1("SELECT id FROM classes LIMIT 1")
            sub = core.q1("SELECT id FROM subjects LIMIT 1")
            exam = core.q1("SELECT id FROM exams LIMIT 1")
            if not exam:
                core.ex("INSERT INTO exams(name, term, year, out_of, status) VALUES('Mid Term', 'Term 1', 2026, 100, 'open')")
                eid = core.q1("SELECT last_insert_rowid() id")['id']
            else:
                eid = exam['id']
            cid = cls['id']
            sid = sub['id']

            # Ensure paper exists
            core.ex("INSERT OR IGNORE INTO exam_papers(exam_id, class_id, subject_id, out_of) VALUES(?,?,?,100)",
                    (eid, cid, sid))
            # Ensure student with mark exists
            st = core.q1("SELECT id FROM students WHERE class_id=? LIMIT 1", (cid,))
            if not st:
                core.ex("INSERT INTO students(adm_no, full_name, class_id, status) VALUES('ADM-P-99', 'Paper Student', ?, 'active')", (cid,))
                st_id = core.q1("SELECT last_insert_rowid() id")['id']
            else:
                st_id = st['id']

            core.ex("INSERT OR REPLACE INTO marks(exam_id, student_id, class_id, subject_id, score) VALUES(?,?,?,?,?)",
                    (eid, st_id, cid, sid, 77.0))
            core.commit()

        # Verify detail page shows delete button
        res = self.client.get(f'/exams/{eid}')
        self.assertIn(f'/exams/{eid}/paper/{cid}/{sid}/delete'.encode(), res.data)

        # Clear marks
        res = self.client.post(f'/exams/{eid}/paper/{cid}/{sid}/clear', data={'csrf_token': self.csrf}, follow_redirects=True)
        self.assertIn(b'cleared', res.data)
        with self.app.app_context():
            count_marks = core.q1("SELECT COUNT(*) n FROM marks WHERE exam_id=? AND class_id=? AND subject_id=?", (eid, cid, sid))['n']
            self.assertEqual(count_marks, 0)
        print("PASS: Clear paper marks verified!")

        # Delete paper entry
        res = self.client.post(f'/exams/{eid}/paper/{cid}/{sid}/delete', data={'csrf_token': self.csrf}, follow_redirects=True)
        self.assertIn(b'deleted', res.data)
        with self.app.app_context():
            paper = core.q1("SELECT id FROM exam_papers WHERE exam_id=? AND class_id=? AND subject_id=?", (eid, cid, sid))
            self.assertIsNone(paper)
        print("PASS: Delete exam paper entry verified!")

    def test_teacher_restrictions_and_privileges(self):
        # Create a teacher account with teaching assignment for only 1 subject
        with self.app.app_context():
            # Setup 2 subjects and 1 class
            core.ex("INSERT OR IGNORE INTO classes(name, stream) VALUES('Grade 8', 'Green')")
            cls = core.q1("SELECT id FROM classes WHERE name='Grade 8' AND stream='Green'")
            core.ex("INSERT OR IGNORE INTO subjects(code, name) VALUES('SCI8', 'Science 8')")
            sub_allowed = core.q1("SELECT id FROM subjects WHERE code='SCI8'")
            core.ex("INSERT OR IGNORE INTO subjects(code, name) VALUES('ART8', 'Art 8')")
            sub_forbidden = core.q1("SELECT id FROM subjects WHERE code='ART8'")
            core.ex("INSERT OR IGNORE INTO exams(name, term, year, out_of, status) VALUES('Term Exam 8', 'Term 2', 2026, 100, 'open')")
            exam = core.q1("SELECT id FROM exams WHERE name='Term Exam 8'")

            # Papers for both subjects
            core.ex("INSERT OR IGNORE INTO exam_papers(exam_id, class_id, subject_id, out_of) VALUES(?,?,?,100)",
                    (exam['id'], cls['id'], sub_allowed['id']))
            core.ex("INSERT OR IGNORE INTO exam_papers(exam_id, class_id, subject_id, out_of) VALUES(?,?,?,100)",
                    (exam['id'], cls['id'], sub_forbidden['id']))

            # Teacher user
            core.ex("INSERT OR REPLACE INTO users(username, full_name, tsc_no, password_hash, role, active) "
                    "VALUES('strictteacher', 'Strict Teacher', 'TSC-8888', ?, 'teacher', 1)",
                    (core.hash_password("teacherpass"),))
            teacher = core.q1("SELECT * FROM users WHERE username='strictteacher'")

            # Assign teacher to ONLY sub_allowed
            core.ex("DELETE FROM teaching WHERE teacher_id=?", (teacher['id'],))
            core.ex("INSERT INTO teaching(teacher_id, class_id, subject_id) VALUES(?,?,?)",
                    (teacher['id'], cls['id'], sub_allowed['id']))
            core.commit()

        # Act as strict teacher
        t_client = self.app.test_client()
        with t_client.session_transaction() as sess:
            sess['uid'] = teacher['id']
            sess['csrf'] = 'teacher_csrf_888'

        try:
            # 1. Allowed subject: can access marks entry (status 200)
            res = t_client.get(f"/marks/enter/{exam['id']}/{cls['id']}/{sub_allowed['id']}")
            self.assertEqual(res.status_code, 200)

            # 2. Forbidden subject: cannot access marks entry (status 403)
            res = t_client.get(f"/marks/enter/{exam['id']}/{cls['id']}/{sub_forbidden['id']}")
            self.assertEqual(res.status_code, 403)

            # 3. Teacher cannot delete student (status 403)
            res = t_client.post('/students/1/delete', data={'csrf_token': 'teacher_csrf_888'})
            self.assertEqual(res.status_code, 403)

            # 4. Teacher cannot delete exam paper (status 403)
            res = t_client.post(f"/exams/{exam['id']}/paper/{cls['id']}/{sub_allowed['id']}/delete", data={'csrf_token': 'teacher_csrf_888'})
            self.assertEqual(res.status_code, 403)

            # 5. Teacher cannot delete user (status 403)
            res = t_client.post(f"/users/{teacher['id']}/delete", data={'csrf_token': 'teacher_csrf_888'})
            self.assertEqual(res.status_code, 403)
        finally:
            with self.app.app_context():
                core.ex("DELETE FROM teaching WHERE teacher_id=?", (teacher['id'],))
                core.ex("DELETE FROM users WHERE id=?", (teacher['id'],))
                core.commit()

        print("PASS: Teacher subject restrictions and unauthorized delete blocks verified!")

    def test_admin_delete_full_exam(self):
        with self.app.app_context():
            core.ex("INSERT INTO exams(name, term, year, out_of, status) VALUES('Exam To Delete', 'Term 3', 2026, 100, 'open')")
            eid = core.q1("SELECT last_insert_rowid() id")['id']
            cls = core.q1("SELECT id FROM classes LIMIT 1")
            sub = core.q1("SELECT id FROM subjects LIMIT 1")
            core.ex("INSERT INTO exam_papers(exam_id, class_id, subject_id, out_of) VALUES(?,?,?,100)", (eid, cls['id'], sub['id']))
            core.commit()

        # Admin deletes full exam
        res = self.client.post(f'/exams/{eid}/delete', data={'csrf_token': self.csrf, 'confirm': 'DELETE'}, follow_redirects=True)
        self.assertIn(b'Examination deleted', res.data)
        with self.app.app_context():
            self.assertIsNone(core.q1("SELECT id FROM exams WHERE id=?", (eid,)))
            self.assertIsNone(core.q1("SELECT id FROM exam_papers WHERE exam_id=?", (eid,)))
        print("PASS: Admin delete full examination verified!")

    def test_tsc_management_and_admin_delete_modal_permissions(self):
        # 1. Admin views /users -> must show TSC Number column and confirmation modal markup
        res = self.client.get('/users')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'TSC Number', res.data)
        self.assertIn(b'deleteModal', res.data)
        self.assertIn(b'Confirm Delete', res.data)

        # 2. Admin adds a new teacher with TSC number
        res = self.client.post('/users/new', data={
            'csrf_token': self.csrf,
            'full_name': 'Kenyan Teacher Alpha',
            'tsc_no': 'TSC-990011',
            'username': 'tr_alpha',
            'role': 'teacher',
            'phone': '0712345678',
            'password': 'Password@123',
            'active': '1'
        }, follow_redirects=True)
        self.assertIn(b'User created', res.data)

        with self.app.app_context():
            teacher = core.q1("SELECT * FROM users WHERE username='tr_alpha'")
            self.assertIsNotNone(teacher)
            self.assertEqual(teacher['tsc_no'], 'TSC-990011')
            t_id = teacher['id']

        try:
            # 3. /users list now displays the teacher, their TSC number, and the Delete button
            res = self.client.get('/users')
            self.assertIn(b'Kenyan Teacher Alpha', res.data)
            self.assertIn(b'TSC-990011', res.data)
            self.assertIn(b'Delete', res.data)

            # 4. Duplicate TSC number (case-insensitive) is prevented
            res_dup = self.client.post('/users/new', data={
                'csrf_token': self.csrf,
                'full_name': 'Kenyan Teacher Beta',
                'tsc_no': 'tsc-990011',  # lowercase duplicate
                'username': 'tr_beta',
                'role': 'teacher',
                'password': 'Password@123',
                'active': '1'
            }, follow_redirects=True)
            self.assertIn(b'That TSC number is already registered', res_dup.data)

            # 5. Non-admin cannot access /users or delete any account
            with self.app.app_context():
                core.ex("UPDATE users SET must_change_password=0 WHERE id=?", (t_id,))
                core.commit()

            t_client = self.app.test_client()
            with t_client.session_transaction() as sess:
                sess['uid'] = t_id
                sess['csrf'] = 't_csrf_token'

            res_unauth_view = t_client.get('/users')
            self.assertEqual(res_unauth_view.status_code, 403)

            res_unauth_del = t_client.post(f'/users/{t_id}/delete', data={'csrf_token': 't_csrf_token'})
            self.assertEqual(res_unauth_del.status_code, 403)

            # 6. Admin cannot delete their own account
            res_del_self = self.client.post(f'/users/{self.admin_id}/delete', data={'csrf_token': self.csrf}, follow_redirects=True)
            self.assertIn(b'You cannot delete your own account', res_del_self.data)

            # 7. Admin can delete the teacher account
            res_del = self.client.post(f'/users/{t_id}/delete', data={'csrf_token': self.csrf}, follow_redirects=True)
            self.assertIn(b'permanently deleted', res_del.data)

            with self.app.app_context():
                self.assertIsNone(core.q1("SELECT id FROM users WHERE id=?", (t_id,)))
        finally:
            with self.app.app_context():
                core.ex("DELETE FROM teaching WHERE teacher_id IN (SELECT id FROM users WHERE username IN ('tr_alpha', 'tr_beta'))")
                core.ex("DELETE FROM users WHERE username IN ('tr_alpha', 'tr_beta')")
                core.commit()

        print("PASS: TSC management, uniqueness validation, and admin delete modal permissions verified!")

if __name__ == '__main__':
    unittest.main()
