"""Django framework configuration for Shubham's code generation.

Rules, golden examples, and file structure for generating production-grade
Django + DRF + PostgreSQL backends.
"""

from __future__ import annotations

from app.agents.frameworks import FrameworkConfig, register_framework

DJANGO_RULES: tuple[str, ...] = (
    "1. Use Django ORM models — NEVER use SQLAlchemy. Inherit from `django.db.models.Model`. "
    "Use `models.CharField`, `models.ForeignKey`, etc.",
    "2. Use Django REST Framework serializers — NEVER use Pydantic. "
    "Use `serializers.ModelSerializer` with `class Meta: model = ...; fields = [...]`",
    "3. Use DRF `ModelViewSet` for CRUD endpoints — use `@action(detail=True/False)` "
    "for custom actions. NEVER use plain function views for API endpoints",
    "4. Use DRF permissions: `IsAuthenticated`, `IsAdminUser`, and custom permission classes — "
    "NEVER skip permission checks on endpoints",
    "5. Use `django-filter` + `DjangoFilterBackend` for queryset filtering — "
    "define `filterset_fields` or custom `FilterSet` classes",
    "6. Use `get_object_or_404()` for single-object lookups — "
    "NEVER manually catch `DoesNotExist` and return 404",
    "7. Use `transaction.atomic()` for multi-step write operations — "
    "import from `django.db`",
    "8. Management commands in `core/management/commands/` — "
    "inherit from `BaseCommand`, implement `handle()` method",
    "9. URL routing: use `DefaultRouter` from DRF and `include()` — "
    "NEVER hardcode URL patterns when a router can auto-generate them",
    "10. Every model MUST have `class Meta` with `ordering`, `verbose_name`, and `db_table` — "
    "add `created_at = models.DateTimeField(auto_now_add=True)` and "
    "`updated_at = models.DateTimeField(auto_now=True)`",
    "11. NEVER use 'pass', '# TODO', '...', or 'raise NotImplementedError' — "
    "every function must have a REAL, COMPLETE implementation",
    "12. NEVER invent import paths — use ONLY names from the contract and previously generated code",
    "13. Authentication: use DRF `TokenAuthentication` or `rest_framework_simplejwt` — "
    "configure in settings `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`",
    "14. Output ONLY the code file — no markdown fences, no explanations, no comments about what to add later",
)

DJANGO_GOLDEN_EXAMPLES: dict[str, str] = {
    "models": '''\
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        db_table = "users"
        ordering = ["-created_at"]
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self) -> str:
        return self.email


class Product(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="products")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "products"
        ordering = ["-created_at"]
''',
    "serializers": '''\
from rest_framework import serializers

from core.models import Product, User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "full_name", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["email", "full_name", "password"]

    def create(self, validated_data: dict) -> User:
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already registered.")
        return value


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "description", "price", "owner", "created_at"]
        read_only_fields = ["id", "owner", "created_at"]
''',
    "views": '''\
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from core.models import Product
from core.permissions import IsOwnerOrReadOnly
from core.serializers import ProductSerializer


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrReadOnly]
    filterset_fields = ["name", "owner"]

    def get_queryset(self):
        return Product.objects.filter(owner=self.request.user).select_related("owner")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=False, methods=["get"])
    def my_products(self, request: Request) -> Response:
        products = self.get_queryset()
        serializer = self.get_serializer(products, many=True)
        return Response(serializer.data)
''',
    "urls": '''\
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.views import ProductViewSet, UserViewSet

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")
router.register(r"products", ProductViewSet, basename="product")

urlpatterns = [
    path("api/v1/", include(router.urls)),
]
''',
}

DJANGO_FILE_STRUCTURE: dict[str, str] = {
    "models": "backend/core/models.py",
    "serializers": "backend/core/serializers.py",
    "permissions": "backend/core/permissions.py",
    "views": "backend/core/views.py",
    "urls": "backend/core/urls.py",
    "services": "backend/core/services.py",
    "tests": "backend/core/tests/",
    "management": "backend/core/management/commands/seed.py",
    "settings": "backend/config/settings.py",
    "wsgi": "backend/config/wsgi.py",
}


DJANGO_CONFIG = FrameworkConfig(
    name="django",
    display_name="Django",
    language="python",
    code_block_lang="python",
    error_comment_prefix="#",
    file_structure=DJANGO_FILE_STRUCTURE,
    rules=DJANGO_RULES,
    golden_examples=DJANGO_GOLDEN_EXAMPLES,
)

register_framework(DJANGO_CONFIG)
