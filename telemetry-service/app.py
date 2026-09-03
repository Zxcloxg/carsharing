"""Telemetry service (Flask).

Single responsibility: ingest high-frequency location/telemetry pings from
cars and answer real-time "where is car X" / "nearest free cars" lookups.

Why Flask here: only a couple of tiny, high-throughput endpoints, no admin
panel / ORM / auth machinery needed, minimal overhead and low latency. The
service is stateless and easy to scale horizontally.

In the skeleton the last-known positions are kept in an in-memory dict.
In production this is a fast key-value store (e.g. Redis).
"""
from datetime import datetime, timezone
from math import radians, sin, cos, asin, sqrt

from flask import Flask, jsonify, request

app = Flask(__name__)

# car_id -> {"lat":..., "lon":..., "status":..., "updated_at":...}
_positions: dict[str, dict] = {}


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


@app.get("/health")
def health():
    """Liveness/readiness probe."""
    return jsonify(
        status="ok",
        service="telemetry-service",
        framework="flask",
        tracked_cars=len(_positions),
        time=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/telemetry")
def ingest():
    """Receive a telemetry ping from a car's on-board device.

    Expected JSON: {"car_id": "A-101", "lat": 55.75, "lon": 37.61,
                    "status": "free", "fuel": 0.8}
    (In later works this endpoint is protected by a device token.)
    """
    data = request.get_json(silent=True) or {}
    car_id = data.get("car_id")
    lat, lon = data.get("lat"), data.get("lon")
    if car_id is None or lat is None or lon is None:
        return jsonify(error="car_id, lat and lon are required"), 400

    _positions[car_id] = {
        "lat": float(lat),
        "lon": float(lon),
        "status": data.get("status", "free"),
        "fuel": data.get("fuel"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return jsonify(accepted=True, car_id=car_id), 200


@app.get("/cars/nearest")
def nearest():
    """Internal lookup used by booking-service: free cars near a point.

    Query params: lat, lon, radius_km (default 3), limit (default 10).
    """
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify(error="lat and lon query params are required"), 400

    radius = float(request.args.get("radius_km", 3))
    limit = int(request.args.get("limit", 10))

    found = []
    for car_id, p in _positions.items():
        if p["status"] != "free":
            continue
        dist = _haversine_km(lat, lon, p["lat"], p["lon"])
        if dist <= radius:
            found.append({"car_id": car_id, "distance_km": round(dist, 3), **p})

    found.sort(key=lambda c: c["distance_km"])
    return jsonify(count=len(found[:limit]), cars=found[:limit])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8020, debug=True)
