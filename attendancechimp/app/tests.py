"""HW6 tests.

HW6 — end-to-end checks for getUploadsForCourse() and /app/getUploads,
plus one test per grading criterion (see the Rubric class).
"""
import datetime as dt
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.utils import timezone

from app.models import (Course, Lecture, QRCodeUpload, InstructorProfile,
                        StudentProfile, getUploadsForCourse)

CHI = timezone.get_default_timezone()   # America/Chicago
UTC = dt.timezone.utc


def at(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=CHI)


class HW6(TestCase):
    def setUp(self):
        self.prof = User.objects.create_user("prof", password="x")
        InstructorProfile.objects.create(user=self.prof)
        self.alice = User.objects.create_user("alice", password="x")
        StudentProfile.objects.create(user=self.alice)
        self.bob = User.objects.create_user("bob", password="x")
        StudentProfile.objects.create(user=self.bob)

        # DATA13600: Mon/Wed/Fri 10:30-11:50
        self.course = Course.objects.create(
            course="DATA13600", name="Data Engineering", instructor=self.prof,
            start_time=dt.time(10, 30), end_time=dt.time(11, 50),
            day_mon=True, day_wed=True, day_fri=True)
        # A second course that must never leak into the results.
        self.other = Course.objects.create(
            course="CS103", name="Intro CS", instructor=self.prof,
            start_time=dt.time(9, 0), end_time=dt.time(10, 0), day_tue=True)

        # 2024-11-04 Mon, 11-05 Tue, 11-06 Wed
        self.lec_mon = Lecture.objects.create(course=self.course, date=at(2024, 11, 4, 10, 30))
        self.lec_wed = Lecture.objects.create(course=self.course, date=at(2024, 11, 6, 10, 30))
        self.lec_cs = Lecture.objects.create(course=self.other, date=at(2024, 11, 5, 9, 0))

    def up(self, student, lecture, when):
        return QRCodeUpload.objects.create(student=student, lecture=lecture, uploaded_at=when)

    def test_day_and_time_filtering(self):
        good_mon = self.up(self.alice, self.lec_mon, at(2024, 11, 4, 10, 35))   # valid
        good_edge_start = self.up(self.bob, self.lec_mon, at(2024, 11, 4, 10, 30))  # boundary, valid
        good_edge_end = self.up(self.bob, self.lec_wed, at(2024, 11, 6, 11, 50))    # boundary, valid
        self.up(self.alice, self.lec_mon, at(2024, 11, 5, 10, 35))   # Tue - wrong day
        self.up(self.alice, self.lec_mon, at(2024, 11, 4, 0, 5))     # midnight Mon
        self.up(self.alice, self.lec_mon, at(2024, 11, 4, 11, 51))   # one min late
        self.up(self.alice, self.lec_mon, at(2024, 11, 9, 10, 35))   # Saturday
        self.up(self.alice, None, at(2024, 11, 4, 10, 35))           # no lecture
        self.up(self.alice, self.lec_cs, at(2024, 11, 5, 9, 30))     # other course

        valid = getUploadsForCourse("DATA13600")
        # Results come back ordered by upload time, so the 10:30 boundary
        # upload precedes the 10:35 one.
        self.assertEqual([u.pk for u in valid],
                         [good_edge_start.pk, good_mon.pk, good_edge_end.pk],
                         "unexpected valid set")

    def test_unknown_course_is_empty(self):
        self.assertEqual(getUploadsForCourse("NOPE999"), [])

    def test_lookup_by_name_fallback(self):
        self.up(self.alice, self.lec_mon, at(2024, 11, 4, 10, 35))
        self.assertEqual(len(getUploadsForCourse("Data Engineering")), 1)

    # ---------------- endpoint ----------------
    def test_endpoint_happy_path(self):
        self.up(self.alice, self.lec_mon, at(2024, 11, 4, 10, 35))
        self.up(self.alice, self.lec_mon, at(2024, 11, 5, 10, 35))  # invalid
        c = Client(); c.login(username="prof", password="x")
        r = c.get("/app/getUploads", {"course": "DATA13600"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [{"username": "alice", "upload_time": "2024-11-04 10:35:00"}])

    def test_endpoint_missing_param(self):
        c = Client(); c.login(username="prof", password="x")
        r = c.get("/app/getUploads")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "Missing course parameter"})

    def test_endpoint_unknown_course_is_empty_list(self):
        c = Client(); c.login(username="prof", password="x")
        r = c.get("/app/getUploads", {"course": "NOPE999"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_endpoint_requires_instructor(self):
        anon = Client()
        self.assertEqual(anon.get("/app/getUploads?course=DATA13600").status_code, 401)
        stu = Client(); stu.login(username="alice", password="x")
        self.assertEqual(stu.get("/app/getUploads?course=DATA13600").status_code, 401)

    def test_endpoint_rejects_post(self):
        c = Client(); c.login(username="prof", password="x")
        self.assertEqual(c.post("/app/getUploads", {"course": "DATA13600"}).status_code, 405)

    # ------------- the upload -> lecture chain actually gets populated ------
    def test_create_upload_links_lecture_via_qr_text(self):
        c = Client(); c.login(username="alice", password="x")
        r = c.post("/app/createQRCodeUpload", {"qr_code_data": self.lec_mon.qr_code_data})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(QRCodeUpload.objects.latest("pk").lecture_id, self.lec_mon.pk)

    def test_create_upload_links_lecture_via_course(self):
        c = Client(); c.login(username="alice", password="x")
        r = c.post("/app/createQRCodeUpload", {"course": "DATA13600"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(QRCodeUpload.objects.latest("pk").lecture_id, self.lec_wed.pk)

    def test_create_course_then_get_uploads(self):
        c = Client(); c.login(username="prof", password="x")
        r = c.post("/app/createCourse", {
            "course": "STAT220", "course-name": "Stats", "start-time": "13:20",
            "end-time": "14:40", "day-tue": "on"})
        self.assertEqual(r.status_code, 200)
        new = Course.objects.get(course="STAT220")
        self.assertEqual((new.start_time, new.end_time, new.day_tue), (dt.time(13, 20), dt.time(14, 40), True))
        lec = Lecture.objects.create(course=new, date=at(2024, 11, 5, 13, 20))
        self.up(self.alice, lec, at(2024, 11, 5, 13, 25))
        r = c.get("/app/getUploads?course=STAT220")
        self.assertEqual(r.json(), [{"username": "alice", "upload_time": "2024-11-05 13:25:00"}])

    def test_hw5_endpoints_still_work(self):
        self.up(self.alice, self.lec_mon, at(2024, 11, 4, 10, 35))
        c = Client(); c.login(username="prof", password="x")
        self.assertEqual(c.get("/app/dumpUploads").status_code, 200)
        self.assertEqual(len(c.get("/app/dumpUploads").json()), 1)
        self.assertEqual(c.post("/app/createLecture", {"choice": "DATA13600"}).status_code, 200)
        self.assertEqual(c.post("/app/createLecture", {"choice": "Data Engineering"}).status_code, 200)

class Rubric(TestCase):
    def setUp(self):
        self.prof = User.objects.create_user("prof", password="x")
        InstructorProfile.objects.create(user=self.prof)
        self.alice = User.objects.create_user("alice", password="x")
        StudentProfile.objects.create(user=self.alice)

        # DATA13600: Mon/Wed/Fri 10:00-11:20  (the rubric's 1.6 example)
        self.c = Course.objects.create(
            course="DATA13600", name="Data Engineering", instructor=self.prof,
            start_time=dt.time(10, 0), end_time=dt.time(11, 20),
            day_mon=True, day_wed=True, day_fri=True)
        self.lec = Lecture.objects.create(course=self.c)

        self.other = Course.objects.create(
            course="CS103", name="Intro CS", instructor=self.prof,
            start_time=dt.time(10, 0), end_time=dt.time(11, 20),
            day_mon=True, day_wed=True, day_fri=True)
        self.other_lec = Lecture.objects.create(course=self.other)

    UNSET = object()  # "caller said nothing" vs. an explicit lecture=None

    def up(self, when, lecture=UNSET, student=None):
        return QRCodeUpload.objects.create(
            student=student or self.alice,
            lecture=self.lec if lecture is self.UNSET else lecture,
            uploaded_at=when)

    def instructor_client(self):
        c = Client(); c.login(username="prof", password="x"); return c

    # ---- 1.1 --------------------------------------------------------------
    def test_1_1_returns_list_of_qrcodeupload_with_the_valid_one(self):
        good = self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI))  # Mon
        out = getUploadsForCourse("DATA13600")
        self.assertIsInstance(out, list)
        self.assertTrue(all(isinstance(u, QRCodeUpload) for u in out))
        self.assertIn(good.pk, [u.pk for u in out])

    # ---- 1.2 --------------------------------------------------------------
    def test_1_2_unknown_course_number_returns_empty_list_no_crash(self):
        self.assertEqual(getUploadsForCourse("DOESNOTEXIST"), [])
        self.assertEqual(getUploadsForCourse(""), [])
        self.assertEqual(getUploadsForCourse(None), [])

    # ---- 1.3 --------------------------------------------------------------
    def test_1_3_other_courses_uploads_excluded(self):
        mine = self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI))
        theirs = self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI), lecture=self.other_lec)
        unlinked = self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI), lecture=None)
        self.assertEqual([u.pk for u in getUploadsForCourse("DATA13600")], [mine.pk])
        self.assertEqual([u.pk for u in getUploadsForCourse("CS103")], [theirs.pk])

    # ---- 1.4 --------------------------------------------------------------
    def test_1_4_day_check(self):
        keep = [self.up(dt.datetime(2024, 11, d, 10, 30, tzinfo=CHI)) for d in (4, 6, 8)]  # Mon Wed Fri
        for d in (5, 7, 9, 10):  # Tue, Thu, Sat, Sun
            self.up(dt.datetime(2024, 11, d, 10, 30, tzinfo=CHI))
        self.assertEqual(sorted(u.pk for u in getUploadsForCourse("DATA13600")),
                         sorted(u.pk for u in keep))

    # ---- 1.5 --------------------------------------------------------------
    def test_1_5_time_check_on_a_meeting_day(self):
        early = self.up(dt.datetime(2024, 11, 4, 9, 59, tzinfo=CHI))
        start = self.up(dt.datetime(2024, 11, 4, 10, 0, tzinfo=CHI))
        mid = self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI))
        end = self.up(dt.datetime(2024, 11, 4, 11, 20, tzinfo=CHI))
        late = self.up(dt.datetime(2024, 11, 4, 11, 21, tzinfo=CHI))
        midnight = self.up(dt.datetime(2024, 11, 4, 0, 0, tzinfo=CHI))
        got = [u.pk for u in getUploadsForCourse("DATA13600")]
        self.assertEqual(got, [start.pk, mid.pk, end.pk])
        for bad in (early, late, midnight):
            self.assertNotIn(bad.pk, got)

    # ---- 1.6 --------------------------------------------------------------
    def test_1_6_utc_storage_checked_in_chicago_time(self):
        # 10:30 Chicago on Mon 2024-11-04 == 16:30 UTC -> counts
        counts = self.up(dt.datetime(2024, 11, 4, 16, 30, tzinfo=UTC))
        # 10:30 UTC == 04:30 Chicago, before class -> does not count
        does_not = self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=UTC))
        got = [u.pk for u in getUploadsForCourse("DATA13600")]
        self.assertEqual(got, [counts.pk])
        self.assertNotIn(does_not.pk, got)

        # And a day that only flips when converted: 2024-11-05 02:00 UTC is
        # Mon 2024-11-04 20:00 Chicago -> Monday is a meeting day but 20:00
        # is outside class, so still out; 2024-11-06 04:00 UTC is Tue 22:00
        # Chicago -> non-meeting day in local terms.
        tue_utc_is_mon_chicago = self.up(dt.datetime(2024, 11, 5, 2, 0, tzinfo=UTC))
        self.assertNotIn(tue_utc_is_mon_chicago.pk,
                         [u.pk for u in getUploadsForCourse("DATA13600")])

    def test_1_6_naive_timestamp_does_not_crash(self):
        # Defensive: if USE_TZ were off, uploaded_at would be naive.
        u = self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI))
        QRCodeUpload.objects.filter(pk=u.pk).update(
            uploaded_at=dt.datetime(2024, 11, 4, 16, 30, tzinfo=UTC))
        self.assertEqual(len(getUploadsForCourse("DATA13600")), 1)

    # ---- 2.1 --------------------------------------------------------------
    def test_2_1_instructor_gets_200_and_json_list(self):
        self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI))
        r = self.instructor_client().get("/app/getUploads?course=DATA13600")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/json")
        self.assertIsInstance(r.json(), list)

    # ---- 2.2 --------------------------------------------------------------
    def test_2_2_student_and_anonymous_get_401(self):
        anon = Client()
        self.assertEqual(anon.get("/app/getUploads?course=DATA13600").status_code, 401)
        stu = Client(); stu.login(username="alice", password="x")
        self.assertEqual(stu.get("/app/getUploads?course=DATA13600").status_code, 401)
        # also 401 before the missing-parameter check
        self.assertEqual(stu.get("/app/getUploads").status_code, 401)

    # ---- 2.3 --------------------------------------------------------------
    def test_2_3_missing_course_param_is_400(self):
        r = self.instructor_client().get("/app/getUploads")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "Missing course parameter"})

    # ---- 2.4 --------------------------------------------------------------
    def test_2_4_unknown_course_is_empty_json_list(self):
        r = self.instructor_client().get("/app/getUploads?course=DOESNOTEXIST")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    # ---- 2.5 --------------------------------------------------------------
    def test_2_5_entry_shape_exact_keys_and_string_time(self):
        self.up(dt.datetime(2024, 11, 5 - 1, 10, 30, tzinfo=CHI))
        body = self.instructor_client().get("/app/getUploads?course=DATA13600").json()
        self.assertEqual(len(body), 1)
        entry = body[0]
        self.assertEqual(set(entry.keys()), {"username", "upload_time"})
        self.assertEqual(entry["username"], "alice")
        self.assertIsInstance(entry["upload_time"], str)
        self.assertEqual(entry["upload_time"], "2024-11-04 10:30:00")
        dt.datetime.strptime(entry["upload_time"], "%Y-%m-%d %H:%M:%S")  # parses

    # ---- 2.6 --------------------------------------------------------------
    def test_2_6_endpoint_matches_helper_exactly(self):
        for when in [dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI),
                     dt.datetime(2024, 11, 4, 23, 0, tzinfo=CHI),
                     dt.datetime(2024, 11, 5, 10, 30, tzinfo=CHI),
                     dt.datetime(2024, 11, 6, 11, 0, tzinfo=CHI),
                     dt.datetime(2024, 11, 9, 10, 30, tzinfo=CHI),
                     dt.datetime(2024, 11, 4, 16, 30, tzinfo=UTC)]:
            self.up(when)
        self.up(dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI), lecture=self.other_lec)

        helper = getUploadsForCourse("DATA13600")
        body = self.instructor_client().get("/app/getUploads?course=DATA13600").json()
        expected = [{"username": u.student.username,
                     "upload_time": timezone.localtime(u.uploaded_at).strftime("%Y-%m-%d %H:%M:%S")}
                    for u in helper]
        self.assertEqual(body, expected)
        self.assertEqual(len(body), len(helper))


class CourseCreationRobustness(TestCase):
    """A grader setting up data in the shell may not know about `course`."""

    def setUp(self):
        self.prof = User.objects.create_user("prof2", password="x")
        InstructorProfile.objects.create(user=self.prof)
        self.stu = User.objects.create_user("carol", password="x")
        StudentProfile.objects.create(user=self.stu)

    def test_two_courses_created_without_course_number_do_not_collide(self):
        a = Course.objects.create(name="DATA13600", instructor=self.prof,
                                  start_time=dt.time(10, 0), end_time=dt.time(11, 20),
                                  day_mon=True)
        b = Course.objects.create(name="CS103", instructor=self.prof,
                                  start_time=dt.time(13, 0), end_time=dt.time(14, 20),
                                  day_tue=True)
        self.assertEqual((a.course, b.course), ("DATA13600", "CS103"))

        lec = Lecture.objects.create(course=a)
        good = QRCodeUpload.objects.create(
            student=self.stu, lecture=lec,
            uploaded_at=dt.datetime(2024, 11, 4, 10, 30, tzinfo=CHI))
        self.assertEqual([u.pk for u in getUploadsForCourse("DATA13600")], [good.pk])
        self.assertEqual(getUploadsForCourse("CS103"), [])

    def test_course_number_only_fills_name(self):
        c = Course.objects.create(course="STAT220", instructor=self.prof,
                                  start_time=dt.time(9, 0), end_time=dt.time(10, 0),
                                  day_wed=True)
        self.assertEqual(c.name, "STAT220")
        lec = Lecture.objects.create(course=c)
        good = QRCodeUpload.objects.create(
            student=self.stu, lecture=lec,
            uploaded_at=dt.datetime(2024, 11, 6, 9, 30, tzinfo=CHI))
        self.assertEqual([u.pk for u in getUploadsForCourse("STAT220")], [good.pk])
