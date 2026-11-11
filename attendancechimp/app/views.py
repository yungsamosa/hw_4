from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from django.utils import timezone

from .models import (
    Course,
    InstructorProfile,
    Lecture,
    QRCodeUpload,
    StudentProfile,
)

CDT = ZoneInfo("America/Chicago")


def index(request: HttpRequest) -> HttpResponse:
    """Render the AttendanceChimp home page.

    Passes the current Chicago time (HH:MM) and the logged-in user's
    information to the template via the context dictionary.
    """
    user = request.user
    context = {
        "current_time": datetime.now(tz=CDT).strftime("%H:%M"),
        "user": user,
        "is_logged_in": user.is_authenticated,
        "username": user.username if user.is_authenticated else "",
    }
    return render(request, "app/index.html", context)


@csrf_exempt  # so a POST reaches require_GET and gets a 405, not a CSRF 403
@require_GET
def new(request: HttpRequest) -> HttpResponse:
    """Render the sign-up form.  GET only; anything else gets a 405."""
    return render(request, "app/new.html")


# Values that mean "this signup is for a student".  The HTML form posts
# "student"/"instructor"; the canonical curl request posts is_student=1.
_STUDENT_VALUES = {"1", "true", "yes", "on", "student"}


def _wants_student(raw: str) -> bool:
    """Interpret the is_student POST field."""
    return raw.strip().lower() in _STUDENT_VALUES


@csrf_exempt
@require_POST
def createUser(request: HttpRequest) -> HttpResponse:
    """Create a User plus a student/instructor profile, then sign them in.

    POST only.  Rejects an email that is already registered.
    """
    email = request.POST.get("email", "").strip()
    user_name = request.POST.get("user_name", "").strip()
    password = request.POST.get("password", "")
    is_student = _wants_student(request.POST.get("is_student", ""))

    if not email or not user_name or not password:
        return HttpResponse(
            "Bad Request: user_name, email and password are required",
            status=400, content_type="text/plain",
        )

    if User.objects.filter(email__iexact=email).exists():
        return HttpResponse(
            f"Conflict: a user with email {email} already exists",
            status=409, content_type="text/plain",
        )

    try:
        with transaction.atomic():
            user = User.objects.create_user(
                username=user_name, email=email, password=password
            )
            if is_student:
                StudentProfile.objects.create(user=user)
            else:
                InstructorProfile.objects.create(user=user)
    except IntegrityError:
        return HttpResponse(
            f"Conflict: the user name {user_name} is already taken",
            status=409, content_type="text/plain",
        )

    login(request, user)

    role = "student" if is_student else "instructor"
    return HttpResponse(
        f"Created {role} {user_name} ({email}) and signed in.",
        status=200, content_type="text/plain",
    )


def time_view(request: HttpRequest) -> HttpResponse:
    """Return current Chicago time as HH:MM (24-hour), plain text."""
    now = datetime.now(tz=CDT)
    return HttpResponse(now.strftime("%H:%M"), content_type="text/plain")


def _decimal_to_str(x: Decimal) -> str:
    """Format Decimal without scientific notation and without trailing zeros."""
    s = format(x, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def sum_view(request: HttpRequest) -> HttpResponse:
    """Return n1 + n2 from query params as plain text."""
    n1_str = request.GET.get("n1", "0")
    n2_str = request.GET.get("n2", "0")

    try:
        val = Decimal(n1_str) + Decimal(n2_str)
    except (InvalidOperation, ValueError):
        return HttpResponse("Bad Request: n1 and n2 must be numbers", status=400, content_type="text/plain")

    return HttpResponse(_decimal_to_str(val), content_type="text/plain")


# ===========================================================================
# HW5 — course / lecture / upload endpoints
# ===========================================================================
#
# Authorization summary:
#
#   /app/createCourse        POST   instructor   else 401
#   /app/createLecture       POST   instructor   else 401
#   /app/createQRCodeUpload  POST   student      else 401
#   /app/dumpUploads         GET    instructor   else 401
#
# Every endpoint checks the role *before* the HTTP method, so an
# unauthorized caller always sees 401 rather than 405.


def _is_instructor(user) -> bool:
    """True when `user` is signed in and has an InstructorProfile."""
    return bool(
        user
        and user.is_authenticated
        and InstructorProfile.objects.filter(user=user).exists()
    )


def _is_student(user) -> bool:
    """True when `user` is signed in and has a StudentProfile."""
    return bool(
        user
        and user.is_authenticated
        and StudentProfile.objects.filter(user=user).exists()
    )


def _unauthorized(message: str) -> HttpResponse:
    return HttpResponse(message, status=401, content_type="text/plain")


def _wrong_method(allowed: str) -> HttpResponse:
    response = HttpResponse(
        f"Method Not Allowed: use {allowed}", status=405, content_type="text/plain"
    )
    response["Allow"] = allowed
    return response


_TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%H%M")


def _parse_time(raw: str):
    """Parse an HTML <input type="time"> value.  Returns None if unparseable.

    Returning None keeps the view off the 500 path: Course.start_time and
    Course.end_time are both nullable.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(raw.upper(), fmt).time()
        except ValueError:
            continue
    return None


# An unchecked checkbox is simply absent from the POST body, so the presence
# of the key is what marks the day as selected.  Explicit negatives are still
# honoured in case the caller is curl rather than a browser.
_DAY_FALSEY = {"0", "false", "off", "no", "none"}


def _day_checked(request: HttpRequest, field: str) -> bool:
    if field not in request.POST:
        return False
    return str(request.POST.get(field, "")).strip().lower() not in _DAY_FALSEY


# ---------------------------------------------------------------------------
# Form pages
# ---------------------------------------------------------------------------


def new_course(request: HttpRequest) -> HttpResponse:
    """Render the 'create a course' form."""
    return render(request, "app/new_course.html")


def new_lecture(request: HttpRequest) -> HttpResponse:
    """Render the 'create a lecture' form, with a course picker."""
    if _is_instructor(request.user):
        courses = Course.objects.filter(instructor=request.user).order_by("name")
    else:
        courses = Course.objects.none()
    return render(request, "app/new_lecture.html", {"courses": courses})


def new_qr_upload(request: HttpRequest) -> HttpResponse:
    """Render the 'upload a QR-code photo' form."""
    return render(request, "app/new_qr_upload.html")


# ---------------------------------------------------------------------------
# /app/createCourse  —  POST, instructor only
# ---------------------------------------------------------------------------


@csrf_exempt
def createCourse(request: HttpRequest) -> HttpResponse:
    """Create a Course from POST data.  Instructors only."""
    if not _is_instructor(request.user):
        return _unauthorized("Unauthorized: you must be logged in as an instructor")

    if request.method != "POST":
        return _wrong_method("POST")

    name = (request.POST.get("course-name") or "").strip()
    if not name:
        return HttpResponse(
            "Bad Request: course-name is required",
            status=400,
            content_type="text/plain",
        )

    fields = {
        "instructor": request.user,
        "start_time": _parse_time(request.POST.get("start-time", "")),
        "end_time": _parse_time(request.POST.get("end-time", "")),
        "day_mon": _day_checked(request, "day-mon"),
        "day_tue": _day_checked(request, "day-tue"),
        "day_wed": _day_checked(request, "day-wed"),
        "day_thu": _day_checked(request, "day-thu"),
        "day_fri": _day_checked(request, "day-fri"),
    }

    # Course.name is unique.  Re-posting the same name updates the existing
    # row instead of raising IntegrityError, so a repeated request is a 200
    # rather than a 500.
    course, created = Course.objects.update_or_create(name=name, defaults=fields)

    verb = "Created" if created else "Updated"
    return HttpResponse(
        f"{verb} course {course.name}", status=200, content_type="text/plain"
    )


# ---------------------------------------------------------------------------
# /app/createLecture  —  POST, instructor only
# ---------------------------------------------------------------------------


@csrf_exempt
def createLecture(request: HttpRequest) -> HttpResponse:
    """Create a Lecture for the course named in POST field `choice`."""
    if not _is_instructor(request.user):
        return _unauthorized("Unauthorized: you must be logged in as an instructor")

    if request.method != "POST":
        return _wrong_method("POST")

    choice = (request.POST.get("choice") or "").strip()
    if not choice:
        return HttpResponse(
            "Bad Request: choice (a course name) is required",
            status=400,
            content_type="text/plain",
        )

    # Course names are unique, so the name identifies the course outright.
    # Prefer one of the caller's own courses if a stale duplicate ever exists.
    course = (
        Course.objects.filter(name=choice, instructor=request.user).first()
        or Course.objects.filter(name=choice).first()
    )
    if course is None:
        return HttpResponse(
            f"Bad Request: no course named {choice}",
            status=400,
            content_type="text/plain",
        )

    lecture = Lecture.objects.create(course=course)

    return HttpResponse(
        f"Created lecture {lecture.pk} for course {course.name}",
        status=200,
        content_type="text/plain",
    )


# ---------------------------------------------------------------------------
# /app/createQRCodeUpload  —  POST, student only
# ---------------------------------------------------------------------------


@csrf_exempt
def createQRCodeUpload(request: HttpRequest) -> HttpResponse:
    """Record a student's uploaded QR-code photograph.

    The student is the logged-in user and the upload time comes from
    QRCodeUpload.uploaded_at (auto_now_add), so the body only has to carry
    the file itself in the `imageUpload` field.
    """
    if not _is_student(request.user):
        return _unauthorized("Unauthorized: you must be logged in as a student")

    if request.method != "POST":
        return _wrong_method("POST")

    # The row is recorded even when no file came through, so that an upload
    # attempt always shows up in /app/dumpUploads.
    upload = QRCodeUpload.objects.create(
        student=request.user,
        image=request.FILES.get("imageUpload"),
    )

    return HttpResponse(
        f"Recorded upload {upload.pk} for {request.user.username}",
        status=200,
        content_type="text/plain",
    )


# ---------------------------------------------------------------------------
# /app/dumpUploads  —  GET, instructor only
# ---------------------------------------------------------------------------


def dumpUploads(request: HttpRequest) -> HttpResponse:
    """Return every QR-code upload as JSON: [{username, upload_time}, ...]."""
    if not _is_instructor(request.user):
        return _unauthorized("Unauthorized: you must be logged in as an instructor")

    if request.method != "GET":
        return _wrong_method("GET")

    data = []
    uploads = (
        QRCodeUpload.objects.select_related("student")
        .order_by("uploaded_at", "pk")
    )
    for upload in uploads:
        stamp = upload.uploaded_at
        if stamp is not None and timezone.is_aware(stamp):
            stamp = timezone.localtime(stamp)
        data.append(
            {
                "username": upload.student.username,
                "upload_time": stamp.strftime("%Y-%m-%d %H:%M:%S") if stamp else "",
            }
        )

    return JsonResponse(data, safe=False)
