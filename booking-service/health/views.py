from django.http import JsonResponse
from django.utils import timezone


def health(request):
    """Liveness/readiness probe.

    Returns 200 with basic service metadata so that a load balancer,
    orchestrator (k8s) or a teammate can verify the service is up.
    """
    return JsonResponse(
        {
            "status": "ok",
            "service": "booking-service",
            "framework": "django",
            "time": timezone.now().isoformat(),
        }
    )
