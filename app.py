import pymysql
pymysql.install_as_MySQLdb()

import os
from flask import Flask, render_template, redirect, url_for, request, flash, session
from flask_wtf.csrf import CSRFProtect
from config import Config
from models import db, User, Service, Booking, BlockedDate, SalonSettings, generate_booking_token
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

app = Flask(__name__)
app.config.from_object(Config)

# ── Admin credentials ──────────────────
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin123'
# ──────────────────────────────────────


db.init_app(app)

# ── CSRF protection (covers every POST form automatically) ──
csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# Read admin credentials from config (which reads from env vars)
ADMIN_USERNAME = app.config['ADMIN_USERNAME']
ADMIN_PASSWORD = app.config['ADMIN_PASSWORD']


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


with app.app_context():
    db.create_all()


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Please sign in to access the admin panel.', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────
@app.route('/')
def home():
    return render_template('index.html')


# ─────────────────────────────────────────────
# BOOK APPOINTMENT (public, no login needed)
# ONE token for ALL services selected
# ─────────────────────────────────────────────
@app.route('/book_appointment', methods=['GET', 'POST'])
def book_appointment():
    services = Service.query.all()

    if request.method == 'POST':
        service_ids = request.form.getlist('service_ids')
        name  = request.form.get('name',  '').strip()
        phone = request.form.get('phone', '').strip()
        date  = request.form.get('date',  '').strip()
        time  = request.form.get('time',  '').strip()

        if not all([name, phone, date, time]):
            flash('All fields are required.', 'error')
            return redirect(url_for('book_appointment'))

        if not service_ids:
            flash('Please select at least one service.', 'error')
            return redirect(url_for('book_appointment'))

        # ── Phone: digits only, 7–15 chars ──
        import re
        if not re.fullmatch(r'\d{7,15}', phone):
            flash('Please enter a valid phone number (digits only, 7–15 digits).', 'error')
            return redirect(url_for('book_appointment'))

        # ── Past date ──
        from datetime import date as dt_date, time as dt_time
        try:
            date_obj = dt_date.fromisoformat(date)
        except ValueError:
            flash('Invalid date.', 'error')
            return redirect(url_for('book_appointment'))

        if date_obj < dt_date.today():
            flash('You cannot book an appointment in the past.', 'error')
            return redirect(url_for('book_appointment'))

        # ── Blocked date ──
        if BlockedDate.query.filter_by(date=date_obj).first():
            flash('The salon is closed on that date. Please choose another day.', 'error')
            return redirect(url_for('book_appointment'))

        # ── Parse time ──
        try:
            h, m   = map(int, time.split(':'))
            time_obj = dt_time(h, m)
        except (ValueError, AttributeError):
            flash('Invalid time selected.', 'error')
            return redirect(url_for('book_appointment'))

        # ── Slot conflict ──
        conflict = Booking.query.filter(
            Booking.date   == date_obj,
            Booking.time   == time_obj,
            Booking.status.in_(['pending', 'confirmed'])
        ).first()
        if conflict:
            flash('That time slot was just taken. Please pick a different time.', 'error')
            return redirect(url_for('book_appointment'))

        user_id = current_user.id if current_user.is_authenticated else None

        try:
            # ── Generate ONE token for this entire booking session ──
            token = generate_booking_token()
            while Booking.query.filter_by(booking_token=token).first():
                token = generate_booking_token()

            # ── Create one row per service, all sharing the same token ──
            for service_id in service_ids:
                row = Booking(
                    booking_token = token,
                    user_id       = user_id,
                    service_id    = int(service_id),
                    name          = name,
                    phone         = phone,
                    date          = date_obj,
                    time          = time_obj,
                    status        = 'pending',
                )
                db.session.add(row)

            db.session.commit()

            return redirect(url_for('booking_confirmation', token=token))

        except Exception as e:
            db.session.rollback()
            flash(f'Error booking appointment: {str(e)}', 'error')
            return redirect(url_for('book_appointment'))

    return render_template('booking.html', services=services,
                           today=__import__('datetime').date.today().isoformat())


# ─────────────────────────────────────────────
# BOOKING CONFIRMATION
# Shows the single shared token
# ─────────────────────────────────────────────
@app.route('/booking_confirmation')
def booking_confirmation():
    token = request.args.get('token', '').strip()

    if not token:
        return redirect(url_for('home'))

    # All rows for this session
    bookings = Booking.query.filter_by(booking_token=token).all()

    if not bookings:
        return redirect(url_for('home'))

    return render_template('booking_confirmation.html',
                           token=token,
                           bookings=bookings)


# ─────────────────────────────────────────────
# CHECK BOOKING STATUS
# Accept:  Booking ID (token)  OR  phone number — not both required
# ─────────────────────────────────────────────
@app.route('/check_booking_status', methods=['GET', 'POST'])
def check_booking_status():
    bookings = []
    error    = None

    if request.method == 'POST':
        query = request.form.get('query', '').strip()

        if not query:
            error = 'Please enter your Booking ID or registered mobile number.'

        else:
            # ── Try token first (format: LUM-XXXXXX) ──
            token_match = Booking.query.filter_by(
                booking_token=query.upper()
            ).all()

            if token_match:
                bookings = token_match

            else:
                # ── Try phone number ──
                phone_match = Booking.query.filter_by(phone=query).all()

                if phone_match:
                    # Group by token so we show one card per session
                    seen   = set()
                    unique = []
                    for b in phone_match:
                        if b.booking_token not in seen:
                            seen.add(b.booking_token)
                            unique.append(b.booking_token)

                    bookings = phone_match

                else:
                    error = 'No bookings found. Please check your Booking ID or mobile number.'

    return render_template('check_booking_status.html',
                           bookings=bookings,
                           error=error,
                           today=__import__('datetime').date.today().isoformat())


# ─────────────────────────────────────────────
# REGISTER / LOGIN / LOGOUT
# ─────────────────────────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name     = request.form.get('name')
        email    = request.form.get('email')
        password = request.form.get('password')

        if not all([name, email, password]):
            flash('All fields are required.', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'error')
            return redirect(url_for('register'))

        try:
            new_user = User(name=name, email=email,
                            password=generate_password_hash(password))
            db.session.add(new_user)
            db.session.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'error')

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form.get('email')
        password = request.form.get('password')

        if not all([email, password]):
            flash('Email and password are required.', 'error')
            return redirect(url_for('login'))

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    services = Service.query.all()
    return render_template('dashboard.html', services=services,
                           name=current_user.name, role=current_user.role)


@app.route('/book/<int:service_id>', methods=['GET', 'POST'])
@login_required
def book_service(service_id):
    service  = Service.query.get_or_404(service_id)
    services = Service.query.all()

    if request.method == 'POST':
        service_ids = request.form.getlist('service_ids')
        date = request.form.get('date')
        time = request.form.get('time')

        if not service_ids or not date or not time:
            flash('Please fill all fields.', 'error')
            return redirect(url_for('book_service', service_id=service_id))

        try:
            token = generate_booking_token()
            while Booking.query.filter_by(booking_token=token).first():
                token = generate_booking_token()

            for sid in service_ids:
                row = Booking(
                    booking_token = token,
                    user_id       = current_user.id,
                    service_id    = int(sid),
                    date          = date,
                    time          = time,
                    status        = 'pending',
                )
                db.session.add(row)

            db.session.commit()
            flash('Appointment booked! 🎉', 'success')
            return redirect(url_for('my_bookings'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'error')

    return render_template('book_service.html', service=service, services=services)


@app.route('/my_bookings')
@login_required
def my_bookings():
    bookings = Booking.query.filter_by(user_id=current_user.id)\
                            .order_by(Booking.created_at.desc()).all()
    return render_template('my_bookings.html', bookings=bookings)


# ─────────────────────────────────────────────
# ADMIN PANEL
# ─────────────────────────────────────────────
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            session.permanent = False
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid credentials.', 'error')

    return render_template('admin_login.html')


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    services = Service.query.order_by(Service.id).all()
    return render_template('admin_dashboard.html', services=services)


@app.route('/admin/services/add', methods=['POST'])
@admin_required
def admin_add_service():
    name  = request.form.get('name',  '').strip()
    price = request.form.get('price', '').strip()

    if not name or not price:
        flash('Name and price are required.', 'error')
        return redirect(url_for('admin_dashboard'))

    try:
        db.session.add(Service(
            name        = name,
            price       = int(price),
            description = request.form.get('description', '').strip() or None,
            duration    = request.form.get('duration',    '').strip() or None,
        ))
        db.session.commit()
        flash(f'Service "{name}" added!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/services/<int:service_id>/edit', methods=['POST'])
@admin_required
def admin_edit_service(service_id):
    s = Service.query.get_or_404(service_id)
    try:
        s.name        = request.form.get('name',        s.name).strip()
        s.price       = int(request.form.get('price',   s.price))
        s.description = request.form.get('description', '').strip() or None
        s.duration    = request.form.get('duration',    '').strip() or None
        db.session.commit()
        flash(f'Service "{s.name}" updated!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/services/<int:service_id>/delete', methods=['POST'])
@admin_required
def admin_delete_service(service_id):
    s = Service.query.get_or_404(service_id)
    name = s.name

    # Block deletion if any bookings reference this service
    booking_count = Booking.query.filter_by(service_id=s.id).count()
    if booking_count > 0:
        flash(
            f'Cannot delete "{name}" — it has {booking_count} booking(s) on record. '
            'Cancel or reassign those bookings first.',
            'error'
        )
        return redirect(url_for('admin_dashboard'))

    try:
        db.session.delete(s)
        db.session.commit()
        flash(f'Service "{name}" deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('admin_dashboard'))


@app.route('/admin/bookings')
@admin_required
def admin_bookings():
    bookings = Booking.query.order_by(Booking.created_at.desc()).all()
    return render_template('admin_bookings.html', bookings=bookings)


@app.route('/admin/bookings/<int:booking_id>/status', methods=['POST'])
@admin_required
def admin_update_booking(booking_id):
    b          = Booking.query.get_or_404(booking_id)
    new_status = request.form.get('status')

    if new_status not in ['pending', 'confirmed', 'completed', 'cancelled']:
        flash('Invalid status.', 'error')
        return redirect(url_for('admin_bookings'))

    try:
        # Update all rows sharing the same token (same session)
        Booking.query.filter_by(booking_token=b.booking_token)\
                     .update({'status': new_status})
        db.session.commit()
        flash(f'Booking {b.booking_token} marked as {new_status}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'error')

    return redirect(url_for('admin_bookings'))


# ─────────────────────────────────────────────
# API: SALON SETTINGS (for frontend slot picker)
# ─────────────────────────────────────────────
@app.route('/api/salon_settings')
def api_salon_settings():
    return {
        'open_time':     SalonSettings.get('open_time',     '10:00'),
        'close_time':    SalonSettings.get('close_time',    '19:30'),
        'slot_interval': int(SalonSettings.get('slot_interval', '30')),
    }


# ─────────────────────────────────────────────
# API: BOOKED SLOTS FOR A DATE
# ─────────────────────────────────────────────
@app.route('/api/booked_slots')
def api_booked_slots():
    from datetime import date as dt_date
    date_str = request.args.get('date', '').strip()
    exclude  = request.args.get('exclude', '').strip()
    try:
        date_obj = dt_date.fromisoformat(date_str)

        # Check if date is blocked
        blocked = BlockedDate.query.filter_by(date=date_obj).first()
        if blocked:
            return {'blocked': True, 'reason': blocked.reason or 'Salon closed', 'booked_times': []}

        q = Booking.query.filter(
            Booking.date == date_obj,
            Booking.status.in_(['pending', 'confirmed'])
        )
        if exclude:
            q = q.filter(Booking.booking_token != exclude)
        booked = q.all()
        times  = list({b.time.strftime('%H:%M') for b in booked if b.time})
        return {'blocked': False, 'booked_times': times}
    except Exception:
        return {'blocked': False, 'booked_times': []}


# ─────────────────────────────────────────────
# CANCEL BOOKING (by token)
# ─────────────────────────────────────────────
@app.route('/cancel_booking', methods=['POST'])
def cancel_booking():
    token = request.form.get('token', '').strip().upper()
    if not token:
        flash('Invalid request.', 'error')
        return redirect(url_for('check_booking_status'))

    rows = Booking.query.filter_by(booking_token=token).all()
    if not rows:
        flash('Booking not found.', 'error')
        return redirect(url_for('check_booking_status'))

    if rows[0].status in ['completed', 'cancelled']:
        flash(f'This booking is already {rows[0].status} and cannot be cancelled.', 'error')
        return redirect(url_for('check_booking_status'))

    try:
        Booking.query.filter_by(booking_token=token).update({'status': 'cancelled'})
        db.session.commit()
        flash(f'Booking {token} has been successfully cancelled.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error cancelling booking: {str(e)}', 'error')

    return redirect(url_for('check_booking_status'))


# ─────────────────────────────────────────────
# RESCHEDULE BOOKING (new date + time)
# ─────────────────────────────────────────────
@app.route('/reschedule_booking', methods=['POST'])
def reschedule_booking():
    from datetime import date as dt_date, time as dt_time
    token    = request.form.get('token',    '').strip().upper()
    new_date = request.form.get('new_date', '').strip()
    new_time = request.form.get('new_time', '').strip()

    if not all([token, new_date, new_time]):
        flash('All fields are required to reschedule.', 'error')
        return redirect(url_for('check_booking_status'))

    rows = Booking.query.filter_by(booking_token=token).all()
    if not rows:
        flash('Booking not found.', 'error')
        return redirect(url_for('check_booking_status'))

    if rows[0].status in ['completed', 'cancelled']:
        flash(f'Cannot reschedule a {rows[0].status} booking.', 'error')
        return redirect(url_for('check_booking_status'))

    try:
        date_obj = dt_date.fromisoformat(new_date)
        h, m     = map(int, new_time.split(':'))
        time_obj = dt_time(h, m)

        # Check if new slot is taken by a DIFFERENT booking
        conflict = Booking.query.filter(
            Booking.date  == date_obj,
            Booking.time  == time_obj,
            Booking.status.in_(['pending', 'confirmed']),
            Booking.booking_token != token
        ).first()

        if conflict:
            flash('That time slot is already booked. Please choose a different time.', 'error')
            return redirect(url_for('check_booking_status'))

        Booking.query.filter_by(booking_token=token).update({
            'date':   date_obj,
            'time':   time_obj,
            'status': 'pending'   # reset to pending after reschedule
        })
        db.session.commit()
        flash(f'Booking {token} has been rescheduled successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error rescheduling: {str(e)}', 'error')

    return redirect(url_for('check_booking_status'))



# ─────────────────────────────────────────────
# ADMIN: ANALYTICS
# ─────────────────────────────────────────────
@app.route('/admin/analytics')
@admin_required
def admin_analytics():
    from datetime import date as dt_date, timedelta as td
    from collections import defaultdict
    from sqlalchemy import func

    today = dt_date.today()

    # ── Stat cards ──
    total_bookings  = Booking.query.count()
    unique_sessions = db.session.query(func.count(func.distinct(Booking.booking_token))).scalar()
    pending_count   = Booking.query.filter_by(status='pending').count()
    revenue_est     = db.session.query(func.sum(Service.price))\
                        .join(Booking, Booking.service_id == Service.id)\
                        .filter(Booking.status.in_(['confirmed','completed'])).scalar() or 0

    # ── Bookings per day — last 14 days ──
    day_labels, day_counts = [], []
    for i in range(13, -1, -1):
        d = today - td(days=i)
        c = Booking.query.filter(Booking.date == d).count()
        day_labels.append(d.strftime('%d %b'))
        day_counts.append(c)

    # ── Bookings per week — last 8 weeks ──
    week_labels, week_counts = [], []
    for i in range(7, -1, -1):
        start = today - td(weeks=i+1)
        end   = today - td(weeks=i)
        c = Booking.query.filter(Booking.date >= start, Booking.date < end).count()
        week_labels.append(f'W{8-i}')
        week_counts.append(c)

    # ── Popular services ──
    svc_data = db.session.query(
        Service.name,
        func.count(Booking.id).label('cnt'),
        func.sum(Service.price).label('rev')
    ).join(Booking, Booking.service_id == Service.id)\
     .group_by(Service.id, Service.name)\
     .order_by(func.count(Booking.id).desc()).all()

    svc_names    = [r.name for r in svc_data]
    svc_counts   = [r.cnt  for r in svc_data]
    svc_revenues = [int(r.rev or 0) for r in svc_data]

    # ── Status breakdown ──
    status_counts = {}
    for st in ['pending','confirmed','completed','cancelled']:
        status_counts[st] = Booking.query.filter_by(status=st).count()

    return render_template('admin_analytics.html',
        total_bookings=total_bookings,
        unique_sessions=unique_sessions,
        pending_count=pending_count,
        revenue_est=revenue_est,
        day_labels=day_labels,
        day_counts=day_counts,
        week_labels=week_labels,
        week_counts=week_counts,
        svc_names=svc_names,
        svc_counts=svc_counts,
        svc_revenues=svc_revenues,
        svc_data=svc_data,
        status_counts=status_counts,
    )


# ─────────────────────────────────────────────
# ADMIN: CALENDAR VIEW
# ─────────────────────────────────────────────
@app.route('/admin/calendar')
@admin_required
def admin_calendar():
    import calendar as cal_mod
    from datetime import date as dt_date
    from collections import defaultdict

    today = dt_date.today()
    year  = int(request.args.get('year',  today.year))
    month = int(request.args.get('month', today.month))

    # clamp month
    if month < 1:  month = 12; year -= 1
    if month > 12: month = 1;  year += 1

    # all bookings this month (use unique tokens)
    from sqlalchemy import extract
    month_bookings = Booking.query.filter(
        extract('year',  Booking.date) == year,
        extract('month', Booking.date) == month,
    ).all()

    # group by date → list of unique token sessions
    by_date = defaultdict(list)
    seen_tokens = defaultdict(set)
    for b in month_bookings:
        ds = b.date.isoformat()
        if b.booking_token not in seen_tokens[ds]:
            seen_tokens[ds].add(b.booking_token)
            by_date[ds].append({
                'token':  b.booking_token,
                'name':   b.name or '—',
                'time':   b.time.strftime('%I:%M %p') if b.time else '—',
                'status': b.status,
            })

    blocked = [str(bd.date) for bd in BlockedDate.query.all()]
    cal_weeks = cal_mod.monthcalendar(year, month)
    month_name = cal_mod.month_name[month]

    # prev / next
    prev_month = month - 1 or 12
    prev_year  = year - (1 if month == 1 else 0)
    next_month = month % 12 + 1
    next_year  = year + (1 if month == 12 else 0)

    return render_template('admin_calendar.html',
        year=year, month=month, month_name=month_name,
        cal_weeks=cal_weeks, by_date=by_date,
        blocked=blocked, today=str(today),
        prev_month=prev_month, prev_year=prev_year,
        next_month=next_month, next_year=next_year,
    )


# ─────────────────────────────────────────────
# ADMIN: SETTINGS (working hours + blocked dates)
# ─────────────────────────────────────────────
@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'save_hours':
            SalonSettings.set('open_time',     request.form.get('open_time',  '10:00'))
            SalonSettings.set('close_time',    request.form.get('close_time', '19:30'))
            SalonSettings.set('slot_interval', request.form.get('slot_interval', '30'))
            db.session.commit()
            flash('Working hours saved successfully!', 'success')

        elif action == 'block_date':
            from datetime import date as dt_date
            date_str = request.form.get('block_date', '').strip()
            reason   = request.form.get('reason', '').strip()
            try:
                date_obj = dt_date.fromisoformat(date_str)
                if not BlockedDate.query.filter_by(date=date_obj).first():
                    db.session.add(BlockedDate(date=date_obj, reason=reason or None))
                    db.session.commit()
                    flash(f'{date_obj.strftime("%d %b %Y")} blocked successfully.', 'success')
                else:
                    flash('That date is already blocked.', 'error')
            except Exception as e:
                flash(f'Error: {str(e)}', 'error')

        elif action == 'unblock_date':
            bd_id = request.form.get('bd_id')
            bd    = BlockedDate.query.get_or_404(int(bd_id))
            db.session.delete(bd)
            db.session.commit()
            flash(f'{bd.date.strftime("%d %b %Y")} unblocked.', 'success')

        return redirect(url_for('admin_settings'))

    blocked_dates = BlockedDate.query.order_by(BlockedDate.date).all()
    settings = {
        'open_time':     SalonSettings.get('open_time',     '10:00'),
        'close_time':    SalonSettings.get('close_time',    '19:30'),
        'slot_interval': SalonSettings.get('slot_interval', '30'),
    }
    import datetime as _dt
    today_date = _dt.date.today()
    return render_template('admin_settings.html',
                           blocked_dates=blocked_dates,
                           settings=settings,
                           today=today_date.isoformat(),
                           today_date=today_date)


# ─────────────────────────────────────────────
# ADMIN: EXPORT BOOKINGS (CSV)
# ─────────────────────────────────────────────
@app.route('/admin/export/bookings')
@admin_required
def admin_export_bookings():
    import csv, io
    from flask import Response

    bookings = Booking.query.order_by(Booking.date.desc(), Booking.time).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Booking ID', 'Customer Name', 'Phone', 'Service',
                     'Date', 'Time', 'Status', 'Price (₹)', 'Booked On'])

    for b in bookings:
        writer.writerow([
            b.booking_token,
            b.name  or '',
            b.phone or '',
            b.service.name if b.service else '',
            b.date.strftime('%d-%m-%Y') if b.date else '',
            b.time.strftime('%I:%M %p') if b.time else '',
            b.status,
            b.service.price if b.service else '',
            b.created_at.strftime('%d-%m-%Y %H:%M') if b.created_at else '',
        ])

    filename = f'lumiere_bookings_{__import__("datetime").date.today().isoformat()}.csv'
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


if __name__ == '__main__':
    # Set FLASK_DEBUG=1 in your shell to enable debug mode during development.
    # Never run with debug=True in production — it exposes an interactive console.
    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)