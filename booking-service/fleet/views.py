"""Маршруты REST API каршеринга.

Слой тонкий: проверить права -> разобрать запрос -> провалидировать ->
вызвать сервис -> отдать ответ с нужным кодом состояния. Никакой
бизнес-логики здесь нет.

Права доступа (подробно — docs/auth.md):
* каталог машин читают все, даже без токена;
* клиент (customer) работает только со своими профилем, ВУ и бронями;
* оператор (operator) проверяет ВУ, блокирует клиентов, меняет статус и
  уровень топлива машин (но не тариф и не госномер), видит все брони;
* администратор (admin) вдобавок добавляет, заменяет и удаляет машины.
"""
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts import services as account_services
from accounts.errors import AccessDenied, CustomerProfileRequired
from accounts.models import Role
from accounts.permissions import (
    ALL_ROLES,
    STAFF_ROLES,
    ensure_fields_allowed,
    ensure_owner_or_staff,
    is_staff,
    require_auth,
    require_role,
)

from . import services
from .serializers import (
    BookingCreateSerializer,
    BookingListQuerySerializer,
    BookingPatchSerializer,
    CarListQuerySerializer,
    CarPatchSerializer,
    CarSerializer,
    CustomerListQuerySerializer,
    CustomerPatchSerializer,
    CustomerSerializer,
    DriverLicenseSerializer,
    LicenseReviewSerializer,
)


# Что оператор меняет у машины при обслуживании. Тариф, госномер, марку, модель,
# год и цвет меняет только администратор (матрица прав — docs/auth.md).
OPERATOR_CAR_FIELDS = frozenset({"status", "fuel_level"})


def _created(item, location):
    return Response(item, status=status.HTTP_201_CREATED, headers={"Location": location})


def _own_customer_id(user):
    """Профиль арендатора текущего клиента; без профиля бронировать и смотреть брони нечего."""
    if not user.customer_id:
        raise CustomerProfileRequired()
    return user.customer_id


class CarCollectionView(APIView):
    """/api/v1/cars — список и создание."""

    def get(self, request):
        query = CarListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(services.list_cars(**query.validated_data), status=status.HTTP_200_OK)

    @require_role(Role.ADMIN)
    def post(self, request):
        body = CarSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        car = services.create_car(body.validated_data)
        return _created(car, f"/api/v1/cars/{car['id']}")


class CarItemView(APIView):
    """/api/v1/cars/{id} — чтение, замена, частичное обновление, удаление."""

    def get(self, request, car_id):
        return Response(services.get_car(car_id), status=status.HTTP_200_OK)

    @require_role(Role.ADMIN)
    def put(self, request, car_id):
        body = CarSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(services.replace_car(car_id, body.validated_data), status=status.HTTP_200_OK)

    @require_role(*STAFF_ROLES)
    def patch(self, request, car_id):
        body = CarPatchSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        if request.user.role != Role.ADMIN:
            ensure_fields_allowed(request.user, body.validated_data, OPERATOR_CAR_FIELDS)
        return Response(services.patch_car(car_id, body.validated_data), status=status.HTTP_200_OK)

    @require_role(Role.ADMIN)
    def delete(self, request, car_id):
        services.delete_car(car_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CarStatusHistoryView(APIView):
    """/api/v1/cars/{id}/status-history — журнал смены статусов машины."""

    @require_role(*STAFF_ROLES)
    def get(self, request, car_id):
        return Response(services.car_status_history(car_id), status=status.HTTP_200_OK)


class CustomerCollectionView(APIView):
    """/api/v1/customers — список клиентов (персонал) и заполнение профиля арендатора."""

    @require_role(*STAFF_ROLES)
    def get(self, request):
        query = CustomerListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(services.list_customers(**query.validated_data), status=status.HTTP_200_OK)

    @require_auth
    def post(self, request):
        body = CustomerSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        if is_staff(request.user):
            # Персонал заводит профиль за клиента (например, в офисе) — без привязки к учётной записи.
            customer = services.create_customer(body.validated_data)
        else:
            customer = account_services.create_customer_profile(request.user, body.validated_data)
        return _created(customer, f"/api/v1/customers/{customer['id']}")


class CustomerItemView(APIView):
    """/api/v1/customers/{id} — чтение клиента, блокировка и разблокировка."""

    @require_auth
    def get(self, request, customer_id):
        customer = services.get_customer(customer_id)
        ensure_owner_or_staff(request.user, customer["id"])
        return Response(customer, status=status.HTTP_200_OK)

    @require_role(*STAFF_ROLES)
    def patch(self, request, customer_id):
        body = CustomerPatchSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(services.patch_customer(customer_id, body.validated_data), status=status.HTTP_200_OK)


class CustomerLicenseCollectionView(APIView):
    """/api/v1/customers/{id}/driver-licenses — заявки клиента и подача новой."""

    @require_auth
    def get(self, request, customer_id):
        customer = services.get_customer(customer_id)
        ensure_owner_or_staff(request.user, customer["id"])
        return Response(services.list_licenses(customer["id"]), status=status.HTTP_200_OK)

    @require_auth
    def post(self, request, customer_id):
        customer = services.get_customer(customer_id)
        ensure_owner_or_staff(request.user, customer["id"])
        body = DriverLicenseSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        license_ = services.submit_license(customer["id"], body.validated_data)
        return _created(license_, f"/api/v1/driver-licenses/{license_['id']}")


class DriverLicenseItemView(APIView):
    """/api/v1/driver-licenses/{id} — чтение заявки и решение оператора."""

    @require_auth
    def get(self, request, license_id):
        license_ = services.get_license(license_id)
        ensure_owner_or_staff(request.user, license_["customer_id"])
        return Response(license_, status=status.HTTP_200_OK)

    @require_role(*STAFF_ROLES)
    def patch(self, request, license_id):
        body = LicenseReviewSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(services.review_license(license_id, body.validated_data), status=status.HTTP_200_OK)


class BookingCollectionView(APIView):
    """/api/v1/bookings — список и создание брони."""

    @require_auth
    def get(self, request):
        query = BookingListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        filters = dict(query.validated_data)
        if not is_staff(request.user):
            # Клиент видит только свои брони; чужой customer_id в фильтре — попытка заглянуть в чужие данные.
            own_id = _own_customer_id(request.user)
            if "customer_id" in filters:
                ensure_owner_or_staff(request.user, filters["customer_id"])
            filters["customer_id"] = own_id
        return Response(services.list_bookings(**filters), status=status.HTTP_200_OK)

    @require_role(*ALL_ROLES)
    def post(self, request):
        body = BookingCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = dict(body.validated_data)
        if is_staff(request.user):
            if not data.get("customer_phone"):
                raise ValidationError({"customer_phone": "Required when staff books on behalf of a customer"})
        else:
            # Клиент бронирует только на себя: телефон берётся из его профиля, а не из запроса.
            own = services.get_customer(_own_customer_id(request.user))
            if data.get("customer_phone") not in (None, own["phone"]):
                raise AccessDenied(
                    "Customers can book only for themselves",
                    details=[{"field": "customer_phone", "message": "Omit it or pass your own phone"}],
                )
            data["customer_phone"] = own["phone"]
        booking = services.create_booking(data)
        return _created(booking, f"/api/v1/bookings/{booking['id']}")


class BookingItemView(APIView):
    """/api/v1/bookings/{id} — чтение, завершение или отмена брони (своей — клиент, любой — персонал)."""

    @require_auth
    def get(self, request, booking_id):
        booking = services.get_booking(booking_id)
        ensure_owner_or_staff(request.user, booking["customer_id"])
        return Response(booking, status=status.HTTP_200_OK)

    @require_auth
    def patch(self, request, booking_id):
        """Завершить или отменить. Поездка закрывается сразу (200), списание за неё идёт в фоне:
        в ответе платёж в статусе pending, его судьба — GET /api/v1/payments/{id}."""
        ensure_owner_or_staff(request.user, services.get_booking(booking_id)["customer_id"])
        body = BookingPatchSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        closed = services.close_booking(booking_id, body.validated_data["status"])
        if closed["payment"]:
            closed["payment"] = services.payment_with_task(closed["payment"])
        return Response(closed, status=status.HTTP_200_OK)


class BookingPaymentsView(APIView):
    """/api/v1/bookings/{id}/payments — платежи по аренде и повторная оплата."""

    @require_auth
    def get(self, request, booking_id):
        ensure_owner_or_staff(request.user, services.get_booking(booking_id)["customer_id"])
        return Response(services.list_payments(booking_id), status=status.HTTP_200_OK)

    @require_auth
    def post(self, request, booking_id):
        """Повторить оплату после сбоя: списание ставится в очередь, ответ — сразу 202 Accepted."""
        ensure_owner_or_staff(request.user, services.get_booking(booking_id)["customer_id"])
        payment = services.payment_with_task(services.retry_payment(booking_id))
        return Response(
            payment, status=status.HTTP_202_ACCEPTED, headers={"Location": f"/api/v1/payments/{payment['id']}"}
        )


class PaymentItemView(APIView):
    """/api/v1/payments/{id} — статус платежа и его фоновой задачи (куда смотреть после 202)."""

    @require_auth
    def get(self, request, payment_id):
        payment = services.get_payment(payment_id)
        ensure_owner_or_staff(request.user, services.get_booking(payment["booking_id"])["customer_id"])
        return Response(services.payment_with_task(payment), status=status.HTTP_200_OK)
