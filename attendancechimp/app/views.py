from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import InstructorProfile, StudentProfile

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
