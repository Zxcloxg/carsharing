"""Доступ к таблице customers: создание, чтение, поиск по фильтру и телефону."""
from django.db.models import Exists, OuterRef
from django.utils import timezone

from ..errors import EmailAlreadyUsed, PhoneAlreadyUsed
from ..models import Customer, DriverLicense, LicenseStatus
from .common import iso, parse_id, save_in_savepoint

FIELDS = ("phone", "email", "full_name", "status")


def to_dict(customer):
    return {
        "id": str(customer.id),
        **{field: getattr(customer, field) for field in FIELDS},
        "birth_date": customer.birth_date.isoformat(),
        "has_valid_license": customer.has_valid_license,
        "created_at": iso(customer.created_at),
    }


def _with_license_flag(query):
    """Добавляет вычисляемое поле has_valid_license одним подзапросом EXISTS, без N+1."""
    valid_license = DriverLicense.objects.filter(
        customer=OuterRef("pk"), status=LicenseStatus.APPROVED, expires_on__gte=timezone.localdate()
    )
    return query.annotate(has_valid_license=Exists(valid_license))


class CustomerRepository:
    # --- чтение -----------------------------------------------------------
    def search(self, *, status=None, offset=0, limit=10):
        query = Customer.objects.all()
        if status:
            query = query.filter(status=status)
        total = query.count()
        page = _with_license_flag(query.order_by("created_at"))[offset:offset + limit]
        return [to_dict(c) for c in page], total

    def get(self, customer_id):
        pk = parse_id(customer_id)
        customer = _with_license_flag(Customer.objects.filter(pk=pk)).first() if pk else None
        return to_dict(customer) if customer else None

    def find_by_phone(self, phone):
        customer = _with_license_flag(Customer.objects.filter(phone=phone)).first()
        return to_dict(customer) if customer else None

    # --- запись -----------------------------------------------------------
    def add(self, data):
        customer = Customer(**data)
        save_in_savepoint(customer, {
            "customers_phone_key": lambda: PhoneAlreadyUsed(
                details=[{"field": "phone", "message": f"Phone {customer.phone} is already registered"}]
            ),
            "customers_email_key": lambda: EmailAlreadyUsed(
                details=[{"field": "email", "message": f"Email {customer.email} is already registered"}]
            ),
        })
        return self.get(customer.id)

    def update(self, customer_id, changes):
        Customer.objects.filter(pk=parse_id(customer_id)).update(**changes, updated_at=timezone.now())
        return self.get(customer_id)
