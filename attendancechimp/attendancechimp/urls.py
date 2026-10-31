from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from app import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('app/', include('app.urls')),
    # Slash-less alias: APPEND_SLASH cannot redirect a POST, so without this
    # a POST to /accounts/login would blow up instead of logging the user in.
    path('accounts/login', auth_views.LoginView.as_view(), name='login_noslash'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', views.index, name='root'),
]
