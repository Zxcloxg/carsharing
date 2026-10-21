"""Админка Django — рабочее место оператора (US-4, US-5)."""
from django.contrib import admin
from django.utils import timezone

from .models import (
    Booking,
    Car,
    CarFeature,
    CarModel,
    CarStatusChange,
    Customer,
    DriverLicense,
    Feature,
    Incident,
    IncidentStatus,
    LicenseStatus,
    Payment,
)


class DriverLicenseInline(admin.TabularInline):
    model = DriverLicense
    extra = 0
    fields = ("number", "categories", "expires_on", "status", "reviewed_at")
    readonly_fields = ("reviewed_at",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("full_name", "phone", "email")
    inlines = [DriverLicenseInline]


@admin.register(DriverLicense)
class DriverLicenseAdmin(admin.ModelAdmin):
    list_display = ("number", "customer", "categories", "expires_on", "status", "submitted_at", "reviewed_by")
    list_filter = ("status",)
    search_fields = ("number", "customer__phone", "customer__full_name")
    actions = ["approve"]

    @admin.action(description="Подтвердить выбранные удостоверения")
    def approve(self, request, queryset):
        for license_ in queryset.filter(status=LicenseStatus.PENDING):
            license_.status = LicenseStatus.APPROVED
            license_.reviewed_at = timezone.now()
            license_.reviewed_by = request.user
            license_.save()


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = ("brand", "model", "body_type", "seats", "fuel_type")
    list_filter = ("brand", "fuel_type")


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    list_display = ("code", "name")


class CarFeatureInline(admin.TabularInline):
    model = CarFeature
    extra = 0


@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    list_display = ("plate", "car_model", "year", "status", "price_per_minute", "fuel_level")
    list_filter = ("status", "car_model__brand")
    search_fields = ("plate", "car_model__brand", "car_model__model")
    inlines = [CarFeatureInline]


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "car", "customer", "status", "started_at", "finished_at", "total_cost")
    list_filter = ("status",)
    search_fields = ("customer__phone", "car__plate")
    inlines = [PaymentInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("id", "booking", "amount", "status", "provider_payment_id", "paid_at")
    list_filter = ("status",)


@admin.register(CarStatusChange)
class CarStatusChangeAdmin(admin.ModelAdmin):
    list_display = ("car", "old_status", "new_status", "booking", "changed_at")
    list_filter = ("new_status",)


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("kind", "car", "customer", "status", "created_at", "resolved_at")
    list_filter = ("status", "kind")
    actions = ["resolve"]

    @admin.action(description="Отметить как решённые")
    def resolve(self, request, queryset):
        queryset.filter(status=IncidentStatus.OPEN).update(
            status=IncidentStatus.RESOLVED, resolved_at=timezone.now(), resolved_by=request.user
        )
