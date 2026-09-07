from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash


ALLOWED_FLIGHT_STATUSES = {"scheduled", "boarding", "departed", "landed", "cancelled", "delayed"}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        DATABASE=os.path.join(app.instance_path, "airline.db"),
        TESTING=False,
    )

    if test_config:
        app.config.update(test_config)

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    def init_db() -> None:
        db = get_db()
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name TEXT,
                email TEXT,
                is_admin INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS aircraft (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model TEXT NOT NULL,
                capacity INTEGER NOT NULL CHECK (capacity > 0)
            );

            CREATE TABLE IF NOT EXISTS flights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_id INTEGER NOT NULL,
                aircraft_id INTEGER NOT NULL,
                departure_time TEXT NOT NULL,
                arrival_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                available_seats INTEGER NOT NULL,
                FOREIGN KEY(route_id) REFERENCES routes(id),
                FOREIGN KEY(aircraft_id) REFERENCES aircraft(id)
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                flight_id INTEGER NOT NULL,
                seat_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'confirmed',
                created_at TEXT NOT NULL,
                UNIQUE(flight_id, seat_number),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(flight_id) REFERENCES flights(id)
            );
            """
        )
        db.commit()

    @app.teardown_appcontext
    def close_db(_: object) -> None:
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def parse_token() -> str | None:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header.split(" ", 1)[1].strip()
        return request.headers.get("X-Auth-Token")

    def auth_required(admin: bool = False):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                token = parse_token()
                if not token:
                    return jsonify({"error": "Authentication required"}), 401

                db = get_db()
                user = db.execute(
                    """
                    SELECT users.id, users.username, users.is_admin
                    FROM sessions JOIN users ON users.id = sessions.user_id
                    WHERE sessions.token = ?
                    """,
                    (token,),
                ).fetchone()
                if not user:
                    return jsonify({"error": "Invalid token"}), 401
                if admin and not user["is_admin"]:
                    return jsonify({"error": "Admin access required"}), 403

                g.current_user = user
                return func(*args, **kwargs)

            return wrapper

        return decorator

    @app.route("/health")
    def health_check():
        return jsonify({"status": "ok"})

    @app.route("/register", methods=["POST"])
    def register():
        payload = request.get_json(silent=True) or {}
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", "")).strip()
        full_name = str(payload.get("full_name", "")).strip() or None
        email = str(payload.get("email", "")).strip() or None

        if not username or not password:
            return jsonify({"error": "username and password are required"}), 400

        db = get_db()
        try:
            cursor = db.execute(
                """
                INSERT INTO users (username, password_hash, full_name, email)
                VALUES (?, ?, ?, ?)
                """,
                (username, generate_password_hash(password), full_name, email),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return jsonify({"error": "username already exists"}), 409

        return jsonify({"id": cursor.lastrowid, "username": username}), 201

    @app.route("/login", methods=["POST"])
    def login():
        payload = request.get_json(silent=True) or {}
        username = str(payload.get("username", "")).strip()
        password = str(payload.get("password", "")).strip()

        db = get_db()
        user = db.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Invalid credentials"}), 401

        token = secrets.token_hex(24)
        db.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user["id"], datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
        return jsonify({"token": token})

    @app.route("/flights", methods=["GET"])
    def list_flights():
        conditions: list[str] = []
        values: list[str] = []

        for param, column in (("origin", "routes.origin"), ("destination", "routes.destination"), ("status", "flights.status")):
            value = request.args.get(param)
            if value:
                conditions.append(f"{column} = ?")
                values.append(value)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        db = get_db()
        flights = db.execute(
            f"""
            SELECT flights.id, routes.origin, routes.destination, flights.departure_time, flights.arrival_time,
                   flights.status, flights.available_seats, aircraft.model AS aircraft_model
            FROM flights
            JOIN routes ON routes.id = flights.route_id
            JOIN aircraft ON aircraft.id = flights.aircraft_id
            {where_clause}
            ORDER BY flights.departure_time ASC
            """,
            values,
        ).fetchall()
        return jsonify([dict(row) for row in flights])

    @app.route("/bookings", methods=["POST"])
    @auth_required()
    def create_booking():
        payload = request.get_json(silent=True) or {}
        flight_id = payload.get("flight_id")
        requested_seat = payload.get("seat_number")

        if not isinstance(flight_id, int):
            return jsonify({"error": "flight_id must be an integer"}), 400

        db = get_db()
        flight = db.execute(
            """
            SELECT flights.id, flights.available_seats, aircraft.capacity
            FROM flights JOIN aircraft ON aircraft.id = flights.aircraft_id
            WHERE flights.id = ?
            """,
            (flight_id,),
        ).fetchone()
        if not flight:
            return jsonify({"error": "Flight not found"}), 404
        if flight["available_seats"] <= 0:
            return jsonify({"error": "No available seats"}), 409

        booked = {
            row["seat_number"]
            for row in db.execute(
                "SELECT seat_number FROM bookings WHERE flight_id = ? AND status = 'confirmed'", (flight_id,)
            ).fetchall()
        }

        seat_number: int
        if requested_seat is None:
            seat_number = next((seat for seat in range(1, flight["capacity"] + 1) if seat not in booked), 0)
            if seat_number == 0:
                return jsonify({"error": "No available seats"}), 409
        elif isinstance(requested_seat, int) and 1 <= requested_seat <= flight["capacity"]:
            if requested_seat in booked:
                return jsonify({"error": "Seat already assigned"}), 409
            seat_number = requested_seat
        else:
            return jsonify({"error": "seat_number must be within aircraft capacity"}), 400

        cursor = db.execute(
            """
            INSERT INTO bookings (user_id, flight_id, seat_number, status, created_at)
            VALUES (?, ?, ?, 'confirmed', ?)
            """,
            (g.current_user["id"], flight_id, seat_number, datetime.now(timezone.utc).isoformat()),
        )
        db.execute("UPDATE flights SET available_seats = available_seats - 1 WHERE id = ?", (flight_id,))
        db.commit()

        return jsonify({"id": cursor.lastrowid, "flight_id": flight_id, "seat_number": seat_number, "status": "confirmed"}), 201

    @app.route("/bookings", methods=["GET"])
    @auth_required()
    def list_bookings():
        db = get_db()
        bookings = db.execute(
            """
            SELECT bookings.id, bookings.flight_id, bookings.seat_number, bookings.status, bookings.created_at,
                   routes.origin, routes.destination
            FROM bookings
            JOIN flights ON flights.id = bookings.flight_id
            JOIN routes ON routes.id = flights.route_id
            WHERE bookings.user_id = ?
            ORDER BY bookings.created_at DESC
            """,
            (g.current_user["id"],),
        ).fetchall()
        return jsonify([dict(row) for row in bookings])

    @app.route("/bookings/<int:booking_id>", methods=["DELETE"])
    @auth_required()
    def cancel_booking(booking_id: int):
        db = get_db()
        booking = db.execute(
            "SELECT id, flight_id, status FROM bookings WHERE id = ? AND user_id = ?",
            (booking_id, g.current_user["id"]),
        ).fetchone()
        if not booking:
            return jsonify({"error": "Booking not found"}), 404
        if booking["status"] == "cancelled":
            return jsonify({"error": "Booking already cancelled"}), 409

        db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        db.execute("UPDATE flights SET available_seats = available_seats + 1 WHERE id = ?", (booking["flight_id"],))
        db.commit()
        return jsonify({"id": booking_id, "status": "cancelled"})

    @app.route("/admin/dashboard", methods=["GET"])
    @auth_required(admin=True)
    def admin_dashboard():
        db = get_db()
        summary = {
            "total_flights": db.execute("SELECT COUNT(*) FROM flights").fetchone()[0],
            "total_passengers": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "active_bookings": db.execute("SELECT COUNT(*) FROM bookings WHERE status = 'confirmed'").fetchone()[0],
            "routes": db.execute("SELECT COUNT(*) FROM routes").fetchone()[0],
            "aircraft": db.execute("SELECT COUNT(*) FROM aircraft").fetchone()[0],
        }
        return jsonify(summary)

    @app.route("/admin/routes", methods=["POST"])
    @auth_required(admin=True)
    def create_route():
        payload = request.get_json(silent=True) or {}
        origin = str(payload.get("origin", "")).strip()
        destination = str(payload.get("destination", "")).strip()
        if not origin or not destination:
            return jsonify({"error": "origin and destination are required"}), 400

        db = get_db()
        cursor = db.execute("INSERT INTO routes (origin, destination) VALUES (?, ?)", (origin, destination))
        db.commit()
        return jsonify({"id": cursor.lastrowid, "origin": origin, "destination": destination}), 201

    @app.route("/admin/aircraft", methods=["POST"])
    @auth_required(admin=True)
    def create_aircraft():
        payload = request.get_json(silent=True) or {}
        model = str(payload.get("model", "")).strip()
        capacity = payload.get("capacity")
        if not model or not isinstance(capacity, int) or capacity <= 0:
            return jsonify({"error": "model and positive integer capacity are required"}), 400

        db = get_db()
        cursor = db.execute("INSERT INTO aircraft (model, capacity) VALUES (?, ?)", (model, capacity))
        db.commit()
        return jsonify({"id": cursor.lastrowid, "model": model, "capacity": capacity}), 201

    @app.route("/admin/flights", methods=["POST"])
    @auth_required(admin=True)
    def create_flight():
        payload = request.get_json(silent=True) or {}
        route_id = payload.get("route_id")
        aircraft_id = payload.get("aircraft_id")
        departure_time = str(payload.get("departure_time", "")).strip()
        arrival_time = str(payload.get("arrival_time", "")).strip()
        status = str(payload.get("status", "scheduled")).strip().lower() or "scheduled"

        if status not in ALLOWED_FLIGHT_STATUSES:
            return jsonify({"error": "Invalid flight status"}), 400
        if not isinstance(route_id, int) or not isinstance(aircraft_id, int):
            return jsonify({"error": "route_id and aircraft_id must be integers"}), 400
        if not departure_time or not arrival_time:
            return jsonify({"error": "departure_time and arrival_time are required"}), 400

        db = get_db()
        route_exists = db.execute("SELECT 1 FROM routes WHERE id = ?", (route_id,)).fetchone()
        aircraft = db.execute("SELECT capacity FROM aircraft WHERE id = ?", (aircraft_id,)).fetchone()
        if not route_exists:
            return jsonify({"error": "Route not found"}), 404
        if not aircraft:
            return jsonify({"error": "Aircraft not found"}), 404

        cursor = db.execute(
            """
            INSERT INTO flights (route_id, aircraft_id, departure_time, arrival_time, status, available_seats)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (route_id, aircraft_id, departure_time, arrival_time, status, aircraft["capacity"]),
        )
        db.commit()
        return jsonify({"id": cursor.lastrowid, "status": status}), 201

    @app.route("/admin/flights/<int:flight_id>/status", methods=["PATCH"])
    @auth_required(admin=True)
    def update_flight_status(flight_id: int):
        payload = request.get_json(silent=True) or {}
        status = str(payload.get("status", "")).strip().lower()
        if status not in ALLOWED_FLIGHT_STATUSES:
            return jsonify({"error": "Invalid flight status"}), 400

        db = get_db()
        cursor = db.execute("UPDATE flights SET status = ? WHERE id = ?", (status, flight_id))
        db.commit()
        if cursor.rowcount == 0:
            return jsonify({"error": "Flight not found"}), 404
        return jsonify({"id": flight_id, "status": status})

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    app.run()
