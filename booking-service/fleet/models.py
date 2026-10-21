"""Реляционная модель ядра каршеринга.

Клиенты и их водительские удостоверения, справочник моделей автомобилей,
автопарк с опциями, брони, платежи, история статусов машин и инциденты.

Ограничения целостности продублированы в базе (CHECK, UNIQUE, FOREIGN KEY):
схемы DRF отсекают некорректный ввод на входе в API, а база гарантирует
целостность при любом способе записи — из админки, скрипта или параллельных
запросов.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower


class CustomerStatus(models.TextChoices):
    ACTIVE = "active"
    BLOCKED = "blocked"


class LicenseStatus(models.TextChoices):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FuelType(models.TextChoices):
    PETROL = "petrol"
    DIESEL = "diesel"
    HYBRID = "hybrid"
    ELECTRIC = "electric"


class CarStatus(models.TextChoices):
    FREE = "free"
    BOOKED = "booked"
    IN_RENT = "in_rent"
    SERVICE = "service"


class BookingStatus(models.TextChoices):
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"


class PaymentStatus(models.TextChoices):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class IncidentKind(models.TextChoices):
    DAMAGE = "damage"
    ACCIDENT = "accident"
    DIRTY = "dirty"
    LOW_FUEL = "low_fuel"
    OTHER = "other"


class IncidentStatus(models.TextChoices):
    OPEN = "open"
    RESOLVED = "resolved"


PHONE_REGEX = r"^\+?[0-9]{10,15}$"


# --- клиенты ----------------------------------------------------------------
class Customer(models.Model):
    """Клиент мобильного приложения (арендатор)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    phone = models.CharField(max_length=16, unique=True, db_comment="Телефон — логин клиента")
    email = models.EmailField(max_length=254, unique=True, null=True, blank=True)
    full_name = models.CharField(max_length=150)
    birth_date = models.DateField()
    status = models.CharField(max_length=10, choices=CustomerStatus.choices, default=CustomerStatus.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customers"
        db_table_comment = "Клиенты (арендаторы)"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(phone__regex=PHONE_REGEX), name="customers_phone_format"),
            models.CheckConstraint(condition=Q(birth_date__gte="1900-01-01"), name="customers_birth_date_min"),
            models.CheckConstraint(condition=Q(status__in=CustomerStatus.values), name="customers_status_valid"),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.phone})"


class DriverLicense(models.Model):
    """Водительское удостоверение клиента и результат его проверки оператором (US-4)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Заявки — история клиента: отклонённую можно подать заново, поэтому 1:N.
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="driver_licenses")
    number = models.CharField(max_length=20, db_comment="Номер удостоверения")
    categories = models.CharField(max_length=30, db_comment="Открытые категории через запятую, например B,C")
    issued_on = models.DateField()
    expires_on = models.DateField()
    status = models.CharField(max_length=10, choices=LicenseStatus.choices, default=LicenseStatus.PENDING)
    reject_reason = models.TextField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # Оператор из таблицы auth_user (Django admin). SET NULL: увольнение оператора не стирает историю проверок.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "driver_licenses"
        db_table_comment = "Водительские удостоверения и их верификация"
        ordering = ["submitted_at"]
        constraints = [
            # У клиента одна «живая» заявка: на проверке или подтверждённая.
            models.UniqueConstraint(
                fields=["customer"], condition=Q(status__in=["pending", "approved"]),
                name="driver_licenses_one_live_per_customer",
            ),
            # Одно физическое удостоверение нельзя подтвердить для двух аккаунтов.
            models.UniqueConstraint(
                fields=["number"], condition=Q(status="approved"), name="driver_licenses_number_approved_uniq"
            ),
            models.CheckConstraint(condition=Q(expires_on__gt=F("issued_on")), name="driver_licenses_dates_order"),
            models.CheckConstraint(
                condition=Q(categories__regex=r"(^|,)B(,|$)"), name="driver_licenses_category_b"
            ),
            models.CheckConstraint(condition=Q(status__in=LicenseStatus.values), name="driver_licenses_status_valid"),
            # Заявка на проверке ещё не рассмотрена, рассмотренная — обязательно с датой.
            models.CheckConstraint(
                condition=Q(status="pending", reviewed_at__isnull=True)
                | (~Q(status="pending") & Q(reviewed_at__isnull=False)),
                name="driver_licenses_reviewed_at_matches_status",
            ),
            models.CheckConstraint(
                condition=~Q(status="rejected") | Q(reject_reason__isnull=False),
                name="driver_licenses_reject_has_reason",
            ),
        ]
        indexes = [
            # Очередь оператора: заявки на проверке в порядке подачи. Частичный индекс
            # хранит только pending-строки, поэтому остаётся маленьким.
            models.Index(fields=["submitted_at"], condition=Q(status="pending"), name="driver_licenses_pending_idx"),
        ]

    def __str__(self):
        return f"{self.number} ({self.status})"


# --- автопарк ---------------------------------------------------------------
class CarModel(models.Model):
    """Справочник моделей: марка, модель и характеристики, общие для всех машин этой модели."""

    id = models.SmallAutoField(primary_key=True)
    brand = models.CharField(max_length=50)
    model = models.CharField(max_length=50)
    body_type = models.CharField(max_length=20, null=True, blank=True, db_comment="Тип кузова")
    seats = models.PositiveSmallIntegerField(null=True, blank=True)
    fuel_type = models.CharField(max_length=10, choices=FuelType.choices, null=True, blank=True)

    class Meta:
        db_table = "car_models"
        db_table_comment = "Справочник моделей автомобилей"
        ordering = ["brand", "model"]
        constraints = [
            # Уникальность без учёта регистра: «Kia Rio» и «KIA rio» — одна модель.
            models.UniqueConstraint(Lower("brand"), Lower("model"), name="car_models_brand_model_uniq"),
            models.CheckConstraint(
                condition=Q(seats__isnull=True) | Q(seats__gte=2, seats__lte=9), name="car_models_seats_range"
            ),
            models.CheckConstraint(
                condition=Q(fuel_type__isnull=True) | Q(fuel_type__in=FuelType.values), name="car_models_fuel_type_valid"
            ),
        ]

    def __str__(self):
        return f"{self.brand} {self.model}"


class Feature(models.Model):
    """Опция автомобиля: детское кресло, полный привод, транспондер и т. п."""

    id = models.SmallAutoField(primary_key=True)
    code = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "features"
        db_table_comment = "Справочник опций автомобиля"
        ordering = ["code"]

    def __str__(self):
        return self.name


class Car(models.Model):
    """Автомобиль автопарка."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # PROTECT: модель из справочника нельзя удалить, пока на ней числятся машины.
    car_model = models.ForeignKey(CarModel, on_delete=models.PROTECT, related_name="cars")
    plate = models.CharField(max_length=10, unique=True, db_comment="Госномер, уникален в автопарке")
    year = models.PositiveSmallIntegerField()
    color = models.CharField(max_length=30, null=True, blank=True)
    price_per_minute = models.DecimalField(max_digits=8, decimal_places=2, db_comment="Тариф, тенге/мин")
    fuel_level = models.FloatField(default=1.0, db_comment="Доля топлива или заряда, 0..1")
    status = models.CharField(max_length=10, choices=CarStatus.choices, default=CarStatus.FREE)
    features = models.ManyToManyField(Feature, through="CarFeature", related_name="cars", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cars"
        db_table_comment = "Автомобили автопарка"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(price_per_minute__gt=0), name="cars_price_positive"),
            models.CheckConstraint(condition=Q(fuel_level__gte=0, fuel_level__lte=1), name="cars_fuel_level_range"),
            models.CheckConstraint(condition=Q(year__gte=1990), name="cars_year_min"),
            models.CheckConstraint(condition=Q(status__in=CarStatus.values), name="cars_status_valid"),
        ]
        indexes = [
            # Самый частый запрос — список свободных машин (GET /api/v1/cars?status=free):
            # WHERE status = ? ORDER BY created_at LIMIT ?. Индекс обслуживает и фильтр, и сортировку.
            models.Index(fields=["status", "created_at"], name="cars_status_created_idx"),
        ]

    def __str__(self):
        return f"{self.plate} {self.car_model}"


class CarFeature(models.Model):
    """Связь N:N «машина — опция»."""

    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="car_features")
    feature = models.ForeignKey(Feature, on_delete=models.PROTECT, related_name="car_features")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "car_features"
        db_table_comment = "Опции конкретных машин (связь N:N)"
        constraints = [
            models.UniqueConstraint(fields=["car", "feature"], name="car_features_car_feature_uniq"),
        ]

    def __str__(self):
        return f"{self.car_id}: {self.feature_id}"


# --- брони и деньги ---------------------------------------------------------
class Booking(models.Model):
    """Аренда машины клиентом: от бронирования до завершения или отмены."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # PROTECT: машину и клиента с историей броней удалить нельзя — это финансовая история.
    car = models.ForeignKey(Car, on_delete=models.PROTECT, related_name="bookings")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="bookings")
    status = models.CharField(max_length=10, choices=BookingStatus.choices, default=BookingStatus.ACTIVE)
    price_per_minute = models.DecimalField(
        max_digits=8, decimal_places=2, db_comment="Тариф машины, зафиксированный в момент брони"
    )
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = "bookings"
        db_table_comment = "Брони и аренды машин"
        ordering = ["started_at"]
        constraints = [
            # Одна машина — не больше одной активной брони (частичный уникальный индекс).
            models.UniqueConstraint(
                fields=["car"], condition=Q(status="active"), name="bookings_one_active_per_car"
            ),
            # Один клиент — не больше одной активной аренды.
            models.UniqueConstraint(
                fields=["customer"], condition=Q(status="active"), name="bookings_one_active_per_customer"
            ),
            models.CheckConstraint(condition=Q(status__in=BookingStatus.values), name="bookings_status_valid"),
            models.CheckConstraint(condition=Q(price_per_minute__gt=0), name="bookings_price_positive"),
            models.CheckConstraint(
                condition=Q(total_cost__isnull=True) | Q(total_cost__gte=0), name="bookings_total_cost_non_negative"
            ),
            models.CheckConstraint(
                condition=Q(finished_at__isnull=True) | Q(finished_at__gte=F("started_at")),
                name="bookings_finish_after_start",
            ),
            # Активная бронь не имеет времени завершения, закрытая — обязательно имеет.
            models.CheckConstraint(
                condition=Q(status="active", finished_at__isnull=True)
                | (~Q(status="active") & Q(finished_at__isnull=False)),
                name="bookings_finished_at_matches_status",
            ),
        ]
        indexes = [
            # История аренд клиента: GET /api/v1/bookings?customer_id=... -> WHERE customer_id = ? ORDER BY started_at.
            models.Index(fields=["customer", "started_at"], name="bookings_customer_started_idx"),
        ]

    def __str__(self):
        return f"{self.car_id} {self.status} {self.started_at:%Y-%m-%d %H:%M}"


class Payment(models.Model):
    """Списание за аренду. Неуспешные попытки сохраняются, поэтому у брони платежей может быть несколько."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    provider_payment_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True, db_comment="Идентификатор операции у платёжного провайдера"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payments"
        db_table_comment = "Платежи за аренду"
        ordering = ["created_at"]
        constraints = [
            # За одну аренду деньги списываются успешно не больше одного раза.
            models.UniqueConstraint(
                fields=["booking"], condition=Q(status="paid"), name="payments_one_paid_per_booking"
            ),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payments_amount_positive"),
            models.CheckConstraint(condition=Q(status__in=PaymentStatus.values), name="payments_status_valid"),
            models.CheckConstraint(
                condition=Q(status__in=["paid", "refunded"], paid_at__isnull=False)
                | (~Q(status__in=["paid", "refunded"]) & Q(paid_at__isnull=True)),
                name="payments_paid_at_matches_status",
            ),
        ]

    def __str__(self):
        return f"{self.amount} {self.status}"


# --- эксплуатация -----------------------------------------------------------
class CarStatusChange(models.Model):
    """Журнал смены статусов машины: когда, с какого на какой и в рамках какой брони (US-5)."""

    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="status_changes")
    old_status = models.CharField(max_length=10, choices=CarStatus.choices, null=True, blank=True)
    new_status = models.CharField(max_length=10, choices=CarStatus.choices)
    booking = models.ForeignKey(
        Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name="car_status_changes"
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "car_status_history"
        db_table_comment = "История статусов машин"
        ordering = ["changed_at"]
        constraints = [
            models.CheckConstraint(condition=Q(new_status__in=CarStatus.values), name="car_status_history_new_valid"),
            models.CheckConstraint(
                condition=Q(old_status__isnull=True) | ~Q(old_status=F("new_status")),
                name="car_status_history_status_changed",
            ),
        ]
        indexes = [
            models.Index(fields=["car", "changed_at"], name="car_status_history_car_idx"),
        ]

    def __str__(self):
        return f"{self.car_id}: {self.old_status} -> {self.new_status}"


class Incident(models.Model):
    """Инцидент с машиной: повреждение, ДТП, грязный салон. Разбирается оператором (US-5)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    car = models.ForeignKey(Car, on_delete=models.PROTECT, related_name="incidents")
    booking = models.ForeignKey(Booking, on_delete=models.PROTECT, null=True, blank=True, related_name="incidents")
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, null=True, blank=True, related_name="incidents")
    kind = models.CharField(max_length=10, choices=IncidentKind.choices)
    description = models.TextField()
    status = models.CharField(max_length=10, choices=IncidentStatus.choices, default=IncidentStatus.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "incidents"
        db_table_comment = "Инциденты с машинами"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(kind__in=IncidentKind.values), name="incidents_kind_valid"),
            models.CheckConstraint(condition=Q(status__in=IncidentStatus.values), name="incidents_status_valid"),
            models.CheckConstraint(
                condition=Q(status="open", resolved_at__isnull=True)
                | Q(status="resolved", resolved_at__isnull=False),
                name="incidents_resolved_at_matches_status",
            ),
        ]
        indexes = [
            # Панель оператора: открытые инциденты по времени.
            models.Index(fields=["created_at"], condition=Q(status="open"), name="incidents_open_idx"),
        ]

    def __str__(self):
        return f"{self.kind} {self.car_id} ({self.status})"
