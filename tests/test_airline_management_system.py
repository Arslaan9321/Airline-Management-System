import sqlite3

from airline_management_system import create_app


def _token_headers(token: str) -> dict[str, str]:
    return {"X-Auth-Token": token}


def _register_and_login(client, username: str, password: str = "secret123") -> str:
    register = client.post("/register", json={"username": username, "password": password})
    assert register.status_code == 201
    login = client.post("/login", json={"username": username, "password": password})
    assert login.status_code == 200
    return login.get_json()["token"]


def _promote_admin(db_path: str, username: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE users SET is_admin = 1 WHERE username = ?", (username,))
        connection.commit()
    finally:
        connection.close()


def _create_admin_flight(client, admin_token: str) -> int:
    route = client.post("/admin/routes", json={"origin": "LHE", "destination": "DXB"}, headers=_token_headers(admin_token))
    aircraft = client.post("/admin/aircraft", json={"model": "A320", "capacity": 2}, headers=_token_headers(admin_token))
    flight = client.post(
        "/admin/flights",
        json={
            "route_id": route.get_json()["id"],
            "aircraft_id": aircraft.get_json()["id"],
            "departure_time": "2026-01-01T10:00:00Z",
            "arrival_time": "2026-01-01T12:00:00Z",
        },
        headers=_token_headers(admin_token),
    )
    assert route.status_code == 201
    assert aircraft.status_code == 201
    assert flight.status_code == 201
    return flight.get_json()["id"]


def test_registration_login_and_flight_search(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app({"TESTING": True, "DATABASE": db_path})

    with app.test_client() as client:
        _register_and_login(client, "admin")
        _promote_admin(db_path, "admin")
        admin_token = client.post("/login", json={"username": "admin", "password": "secret123"}).get_json()["token"]

        _create_admin_flight(client, admin_token)
        flights = client.get("/flights", query_string={"origin": "LHE", "destination": "DXB"})

        assert flights.status_code == 200
        payload = flights.get_json()
        assert len(payload) == 1
        assert payload[0]["available_seats"] == 2


def test_booking_assignment_and_cancellation(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app({"TESTING": True, "DATABASE": db_path})

    with app.test_client() as client:
        _register_and_login(client, "admin")
        _promote_admin(db_path, "admin")
        admin_token = client.post("/login", json={"username": "admin", "password": "secret123"}).get_json()["token"]
        flight_id = _create_admin_flight(client, admin_token)

        passenger_token = _register_and_login(client, "passenger")
        booking = client.post("/bookings", json={"flight_id": flight_id}, headers=_token_headers(passenger_token))
        assert booking.status_code == 201
        seat_number = booking.get_json()["seat_number"]
        assert seat_number == 1

        duplicate = client.post(
            "/bookings",
            json={"flight_id": flight_id, "seat_number": seat_number},
            headers=_token_headers(passenger_token),
        )
        assert duplicate.status_code == 409

        cancellation = client.delete(f"/bookings/{booking.get_json()['id']}", headers=_token_headers(passenger_token))
        assert cancellation.status_code == 200

        flights = client.get("/flights")
        assert flights.get_json()[0]["available_seats"] == 2


def test_admin_dashboard_and_flight_status_update(tmp_path):
    db_path = str(tmp_path / "test.db")
    app = create_app({"TESTING": True, "DATABASE": db_path})

    with app.test_client() as client:
        _register_and_login(client, "admin")
        _promote_admin(db_path, "admin")
        admin_token = client.post("/login", json={"username": "admin", "password": "secret123"}).get_json()["token"]
        flight_id = _create_admin_flight(client, admin_token)

        status_update = client.patch(
            f"/admin/flights/{flight_id}/status",
            json={"status": "boarding"},
            headers=_token_headers(admin_token),
        )
        dashboard = client.get("/admin/dashboard", headers=_token_headers(admin_token))

        assert status_update.status_code == 200
        assert status_update.get_json()["status"] == "boarding"
        assert dashboard.status_code == 200
        assert dashboard.get_json()["total_flights"] == 1
