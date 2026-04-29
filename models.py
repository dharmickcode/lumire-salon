from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone, timedelta

# India Standard Time = UTC + 5:30
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST).replace(tzinfo=None)
import random
import string

db = SQLAlchemy()


def generate_booking_token():
    chars = string.ascii_uppercase + string.digits
    code  = ''.join(random.choices(chars, k=6))
    return f'LUM-{code}'


class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id        = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(100), nullable=False)
    email     = db.Column(db.String(100), unique=True, nullable=False)
    password  = db.Column(db.String(200), nullable=False)
    role      = db.Column(db.String(20), default='user')
    is_active = db.Column(db.Boolean, default=True)
    bookings  = db.relationship('Booking', backref='user', lazy=True)


class Service(db.Model):
    __tablename__ = 'service'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    price       = db.Column(db.Integer, nullable=False)
    duration    = db.Column(db.String(50),  nullable=True)
    description = db.Column(db.Text,        nullable=True)
    bookings    = db.relationship('Booking', backref='service', lazy=True)


class Booking(db.Model):
    __tablename__ = 'booking'
    id            = db.Column(db.Integer, primary_key=True)
    booking_token = db.Column(db.String(20), nullable=False, index=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'),    nullable=True)
    service_id    = db.Column(db.Integer, db.ForeignKey('service.id'), nullable=False)
    name          = db.Column(db.String(100), nullable=True)
    phone         = db.Column(db.String(20),  nullable=True)
    date          = db.Column(db.Date,   nullable=False)
    time          = db.Column(db.Time,   nullable=False)
    status        = db.Column(db.String(20), default='pending')
    created_at    = db.Column(db.DateTime, default=now_ist)


class BlockedDate(db.Model):
    """Dates the salon is closed — no bookings allowed."""
    __tablename__ = 'blocked_date'
    id         = db.Column(db.Integer, primary_key=True)
    date       = db.Column(db.Date, nullable=False, unique=True)
    reason     = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=now_ist)


class SalonSettings(db.Model):
    """Key-value store for salon configuration."""
    __tablename__ = 'salon_settings'
    id    = db.Column(db.Integer, primary_key=True)
    key   = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(200), nullable=False)

    @staticmethod
    def get(key, default=None):
        row = SalonSettings.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = SalonSettings.query.filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.session.add(SalonSettings(key=key, value=value))