# Airline Management System

A Python web application for managing airline operations, flights, passengers, schedules, and ticket bookings from a centralized platform.

## Features

- User registration and secure login (password hashing + token auth)
- Flight search by origin, destination, and status
- Online booking with seat assignment and availability tracking
- Booking confirmation and cancellation
- Passenger booking history
- Flight status management
- Admin dashboard with operational summaries
- Route, aircraft, and flight management
- Centralized SQLite database for records

## Tech Stack

- Python
- Flask
- SQLite
- Pytest

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python /home/runner/work/Airline-Management-System/Airline-Management-System/airline_management_system.py
```

The API runs on `http://127.0.0.1:5000`.

## Main API Endpoints

- `POST /register` – create passenger account
- `POST /login` – authenticate and get token
- `GET /flights` – search and view schedules
- `POST /bookings` – book a flight seat
- `GET /bookings` – view passenger reservations
- `DELETE /bookings/<booking_id>` – cancel reservation
- `GET /admin/dashboard` – admin operations summary
- `POST /admin/routes` – add route
- `POST /admin/aircraft` – add aircraft
- `POST /admin/flights` – add flight schedule
- `PATCH /admin/flights/<flight_id>/status` – update flight status

## Testing

```bash
pytest -q
```
