"""
models.py — Data model for AttendanceChimp.

AttendanceChimp is an electronic class attendance tool.  Instructors create
courses and lectures; each lecture is assigned a random QR code.  Students
photograph the QR code and upload the image.  The system decodes the QR code,
matches it to a lecture, and records the attendance.

Entities
--------
- CourseProfile   : extends Django User → instructor-specific data
- StudentProfile  : extends Django User → student-specific data
- Course          : a course taught by one instructor
- Enrollment      : junction table linking students to courses
- Lecture         : a single class meeting (has a QR-code identifier)
- QRCodeUpload    : a student's uploaded QR-code photograph
"""

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import logging
import string
import random

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User profiles
# ---------------------------------------------------------------------------

class InstructorProfile(models.Model):
    """One-to-one extension of Django User for instructors."""
    user = models.OneToOneField(User, on_delete=models.CASCADE,
                                related_name='instructor_profile')

    def __str__(self):
        return f"Instructor: {self.user.username}"

class StudentProfile(models.Model):
    """One-to-one extension of Django User for students."""
    user = models.OneToOneField(User, on_delete=models.CASCADE,
                                related_name='student_profile')

    def __str__(self):
        return f"Student: {self.user.username}"


# ---------------------------------------------------------------------------
# Course / Enrollment
# ---------------------------------------------------------------------------

class Course(models.Model):
    """A course taught by a single instructor."""
    # The course number (e.g. "DATA13600").  This is the string callers use
    # to identify a course: /app/getUploads?course=DATA13600.
    course = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255, unique=True)
    instructor = models.ForeignKey(User, on_delete=models.CASCADE,
                                   related_name='courses_taught')
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    day_mon = models.BooleanField(default=False)
    day_tue = models.BooleanField(default=False)
    day_wed = models.BooleanField(default=False)
    day_thu = models.BooleanField(default=False)
    day_fri = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        """Keep `course` and `name` populated and distinct per row.

        Both columns are unique, so a caller that supplies only one of them
        would otherwise leave the other at "" — and the second such course
        would collide.  Each falls back to the other.
        """
        if not self.course:
            self.course = (self.name or "")[:64]
        if not self.name:
            self.name = self.course
        super().save(*args, **kwargs)

    def meets_on_day(self, weekday_int):
        """Return True if the course meets on the given weekday (0=Mon).

        Saturday (5) and Sunday (6) are always False: the model only carries
        the five weekday flags, so no course can meet at the weekend.
        """
        weekdays = [self.day_mon, self.day_tue, self.day_wed,
                    self.day_thu, self.day_fri]
        if not 0 <= weekday_int < len(weekdays):
            return False
        return weekdays[weekday_int]

    def __str__(self):
        return f"{self.course} – {self.name}" if self.course else self.name


class Enrollment(models.Model):
    """Junction table: which students are enrolled in which courses."""
    student = models.ForeignKey(User, on_delete=models.CASCADE,
                                related_name='enrollments')
    course = models.ForeignKey(Course, on_delete=models.CASCADE,
                               related_name='enrollments')

    class Meta:
        unique_together = ('student', 'course')

    def __str__(self):
        return f"{self.student.username} → {self.course.name}"


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

def _random_qr_data(length=16):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


class Lecture(models.Model):
    """A single class meeting for a course."""
    course = models.ForeignKey(Course, on_delete=models.CASCADE,
                               related_name='lectures')
    # default= rather than auto_now_add= so a lecture can be recorded for an
    # explicit date (back-filling, fixtures, tests).  New lectures still get
    # "now" when the caller says nothing.
    date = models.DateTimeField(default=timezone.now)
    qr_code_data = models.CharField(max_length=256, unique=True,
                                    default=_random_qr_data)

    def __str__(self):
        return f"{self.course.name} – {self.date}"


# ---------------------------------------------------------------------------
# QR Code Upload
# ---------------------------------------------------------------------------

class QRCodeUpload(models.Model):
    """A photograph of a QR code uploaded by a student."""
    student = models.ForeignKey(User, on_delete=models.CASCADE,
                                related_name='qr_uploads')
    lecture = models.ForeignKey(Lecture, on_delete=models.SET_NULL,
                                null=True, blank=True,
                                related_name='uploads')
    # FileField rather than ImageField: ImageField makes Pillow a hard
    # requirement of `manage.py makemigrations`, and a grading
    # environment may not have Pillow installed.
    image = models.FileField(upload_to='qr_uploads/', null=True, blank=True)
    # default= rather than auto_now_add= for the same reason as Lecture.date:
    # getUploadsForCourse() validates against this timestamp, and an
    # auto_now_add field cannot be given a value at all.
    uploaded_at = models.DateTimeField(default=timezone.now)
    decoded_data = models.CharField(max_length=256, blank=True, default='')
    is_valid = models.BooleanField(default=False)

    def __str__(self):
        return f"Upload by {self.student.username} at {self.uploaded_at}"


# ---------------------------------------------------------------------------
# Helper  functions
# ---------------------------------------------------------------------------

def is_instructor(user):
    """Return True if the user has an InstructorProfile."""
    return hasattr(user, 'instructor_profile')


def is_student(user):
    """Return True if the user has a StudentProfile."""
    return hasattr(user, 'student_profile')


def getUploadsForCourse(course):
    """Return the QRCodeUpload objects that are valid attendance for `course`.

    `course` is a course-number string such as "DATA13600".  An upload counts
    as valid attendance when both of these hold:

      1. it was uploaded on a weekday the course meets, and
      2. its time of day falls within [start_time, end_time].

    Uploads reach the course through the Upload → Lecture → Course foreign-key
    chain, so an upload that was never matched to a lecture is never valid.

    Returns [] when no course has that course number.  A course whose
    start_time or end_time has not been set has no time window to test
    against, so only the meeting-day check is applied to it.
    """
    course_obj = (
        Course.objects.filter(course=course).first()
        # Fall back to the course title: courses created before the course
        # number existed carry the number in `name`.
        or Course.objects.filter(name=course).first()
    )
    if course_obj is None:
        logger.info("getUploadsForCourse: no course with number %r", course)
        return []

    uploads = (
        QRCodeUpload.objects
        .filter(lecture__course=course_obj)
        .select_related("student", "lecture")
        .order_by("uploaded_at", "pk")
    )

    valid = []
    for upload in uploads:
        stamp = upload.uploaded_at
        if stamp is None:
            continue
        # Timestamps are stored in UTC (USE_TZ=True); the course schedule is
        # local wall-clock time, so compare in the project's time zone.
        if timezone.is_aware(stamp):
            stamp = timezone.localtime(stamp)

        if not course_obj.meets_on_day(stamp.weekday()):
            continue

        start, end = course_obj.start_time, course_obj.end_time
        if start is not None and end is not None:
            if not (start <= stamp.time() <= end):
                continue

        valid.append(upload)

    return valid
