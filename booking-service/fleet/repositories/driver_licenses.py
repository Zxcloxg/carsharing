"""Доступ к таблице driver_licenses: подача заявки, чтение, обновление статуса проверки."""
from ..errors import LicenseAlreadySubmitted, LicenseNumberTaken
from ..models import DriverLicense
from .common import iso, parse_id, save_in_savepoint

FIELDS = ("number", "categories", "status", "reject_reason")


def to_dict(license_):
    return {
        "id": str(license_.id),
        "customer_id": str(license_.customer_id),
        **{field: getattr(license_, field) for field in FIELDS},
        "issued_on": license_.issued_on.isoformat(),
        "expires_on": license_.expires_on.isoformat(),
        "submitted_at": iso(license_.submitted_at),
        "reviewed_at": iso(license_.reviewed_at),
    }


def _violations(license_):
    return {
        "driver_licenses_one_live_per_customer": lambda: LicenseAlreadySubmitted(
            details=[{"field": "customer_id", "message": "Customer already has a pending or approved license"}]
        ),
        "driver_licenses_number_approved_uniq": lambda: LicenseNumberTaken(
            details=[{"field": "number", "message": f"License {license_.number} is approved for another customer"}]
        ),
    }


class DriverLicenseRepository:
    # --- чтение -----------------------------------------------------------
    def search(self, *, customer_id=None, status=None):
        query = DriverLicense.objects.all()
        if customer_id:
            query = query.filter(customer_id=customer_id)
        if status:
            query = query.filter(status=status)
        return [to_dict(lic) for lic in query.order_by("submitted_at")]

    def get(self, license_id):
        pk = parse_id(license_id)
        license_ = DriverLicense.objects.filter(pk=pk).first() if pk else None
        return to_dict(license_) if license_ else None

    def get_for_update(self, license_id):
        pk = parse_id(license_id)
        license_ = DriverLicense.objects.select_for_update().filter(pk=pk).first() if pk else None
        return to_dict(license_) if license_ else None

    # --- запись -----------------------------------------------------------
    def add(self, customer_id, data):
        license_ = DriverLicense(customer_id=customer_id, **data)
        return to_dict(save_in_savepoint(license_, _violations(license_)))

    def update(self, license_id, changes):
        license_ = DriverLicense.objects.get(pk=parse_id(license_id))
        for field, value in changes.items():
            setattr(license_, field, value)
        return to_dict(save_in_savepoint(license_, _violations(license_)))
