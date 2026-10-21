"""Доменные ошибки каршеринга.

У каждой ошибки свой HTTP-код и машиночитаемый `code`; в ответ они попадают
в едином формате {"error": {"code", "message", "details"}} — см.
config/exceptions.py.
"""
from rest_framework import status

from config.exceptions import DomainError


class CarNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "car_not_found"
    message = "Car not found"


class PlateAlreadyUsed(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "plate_already_used"
    message = "Car with this plate already exists"


class CarHasBookings(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "car_has_bookings"
    message = "Car has bookings and cannot be deleted"


class CarNotAvailable(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "car_not_available"
    message = "Car is not available for booking"


class BookingNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "booking_not_found"
    message = "Booking not found"


class BookingAlreadyClosed(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "booking_already_closed"
    message = "Booking is already closed"


class CustomerNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "customer_not_found"
    message = "Customer not found"


class PhoneAlreadyUsed(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "phone_already_used"
    message = "Customer with this phone already exists"


class EmailAlreadyUsed(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "email_already_used"
    message = "Customer with this email already exists"


class CustomerCannotRent(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "customer_cannot_rent"
    message = "Customer is not allowed to rent"


class CustomerHasActiveBooking(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "customer_has_active_booking"
    message = "Customer already has an active booking"


class LicenseNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "license_not_found"
    message = "Driver license not found"


class LicenseAlreadySubmitted(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "license_already_submitted"
    message = "Driver license is already submitted"


class LicenseAlreadyReviewed(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "license_already_reviewed"
    message = "Driver license is already reviewed"


class LicenseNumberTaken(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "license_number_taken"
    message = "Driver license is approved for another customer"
