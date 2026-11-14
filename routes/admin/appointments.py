"""Admin appointments: list, schedule, edit, status, delete."""

from flask import (
    render_template, request, redirect, url_for, session, flash, jsonify,
)
from datetime import datetime

from core.db import get_db, migrate_db
from core.helpers import (
    login_required, role_required, log_activity,
)
from routes.admin import admin

def _req_data():
    """Merge JSON body and form into a plain dict-like getter."""
    data = {}
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if isinstance(body, dict):
            data.update(body)
    # form overrides / fills
    for k in request.form:
        data[k] = request.form.get(k)
    return data


def _req_get(data, key, default=''):
    v = data.get(key, default)
    if v is None:
        return default
    return v


def _wants_json():
    return (
        request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in (request.headers.get('Accept') or '')
    )


STATUSES = ('scheduled', 'completed', 'cancelled', 'no_show')
STATUS_LABELS = {
    'scheduled': 'Scheduled',
    'completed': 'Completed',
    'cancelled': 'Cancelled',
    'no_show': 'No show',
}
APPT_TYPES = ('meeting', 'property_visit', 'call', 'follow_up', 'other')
APPT_TYPE_LABELS = {
    'meeting': 'Client meeting',
    'property_visit': 'Property visit',
    'call': 'Call',
    'follow_up': 'Follow-up',
    'other': 'Other',
}


def _log_appt_activity(db, appointment_id, action, details=None):
    try:
        uid = session.get('user_id')
        db.execute(
            'INSERT INTO appointment_activity (appointment_id, user_id, action, details) VALUES (?, ?, ?, ?)',
            (appointment_id, uid, action, details),
        )
        db.commit()
    except Exception:
        pass
    try:
        log_activity(action, 'appointment', appointment_id, details)
    except Exception:
        pass


def _normalize_type(t):
    t = (t or 'meeting').strip().lower().replace(' ', '_').replace('-', '_')
    aliases = {'visit': 'property_visit', 'viewing': 'property_visit', 'followup': 'follow_up', 'phone': 'call'}
    t = aliases.get(t, t)
    if t not in APPT_TYPES:
        t = 'meeting'
    return t


def _normalize_status(st):
    st = (st or 'scheduled').strip().lower().replace(' ', '_')
    aliases = {
        'done': 'completed',
        'complete': 'completed',
        'cancel': 'cancelled',
        'canceled': 'cancelled',
        'noshow': 'no_show',
        'no-show': 'no_show',
    }
    st = aliases.get(st, st)
    if st not in STATUSES:
        st = 'scheduled'
    return st


def _appointments_query(db, q='', status='', agent_id='', date_from='', date_to=''):
    sql = (
        "SELECT a.*, "
        "c.full_name as client_name, "
        "p.title as property_title, "
        "u.full_name as agent_name "
        "FROM appointments a "
        "LEFT JOIN clients c ON a.client_id = c.id "
        "LEFT JOIN properties p ON a.property_id = p.id "
        "LEFT JOIN users u ON a.agent_id = u.id "
        "WHERE 1=1"
    )
    params = []
    if q:
        sql += (
            " AND (IFNULL(c.full_name,'') LIKE ? OR IFNULL(p.title,'') LIKE ? "
            "OR IFNULL(u.full_name,'') LIKE ? OR IFNULL(a.notes,'') LIKE ?)"
        )
        qq = '%' + q + '%'
        params.extend([qq, qq, qq, qq])
    if status in STATUSES:
        sql += " AND lower(IFNULL(a.status,'')) = ?"
        params.append(status)
    if agent_id and str(agent_id).isdigit():
        sql += " AND a.agent_id = ?"
        params.append(int(agent_id))
    if date_from:
        sql += " AND date(a.appointment_date) >= date(?)"
        params.append(date_from)
    if date_to:
        sql += " AND date(a.appointment_date) <= date(?)"
        params.append(date_to)
    sql += " ORDER BY a.appointment_date DESC"
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    for r in rows:
        r['status'] = _normalize_status(r.get('status'))
        r['appointment_type'] = _normalize_type(r.get('appointment_type') or 'meeting')
        try:
            r['duration_minutes'] = int(r.get('duration_minutes') or 60)
        except (TypeError, ValueError):
            r['duration_minutes'] = 60
    return rows


def _form_lists(db):
    clients = db.execute(
        'SELECT id, full_name FROM clients ORDER BY full_name'
    ).fetchall()
    properties = db.execute(
        'SELECT id, title FROM properties ORDER BY title'
    ).fetchall()
    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('agent','admin') AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    return clients, properties, agents


@admin.route('/admin/appointments')
@login_required
@role_required('admin')
def admin_appointments():
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    if status:
        status = _normalize_status(status)
        if status not in STATUSES:
            status = ''
    agent_id = request.args.get('agent_id', '').strip()
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()

    rows = _appointments_query(
        db, q=q, status=status, agent_id=agent_id,
        date_from=date_from, date_to=date_to,
    )
    all_rows = _appointments_query(db)
    stats = {st: sum(1 for r in all_rows if r['status'] == st) for st in STATUSES}
    stats['total'] = len(all_rows)

    today = datetime.now().strftime('%Y-%m-%d')
    stats['upcoming'] = sum(
        1 for r in all_rows
        if r['status'] == 'scheduled' and (r.get('appointment_date') or '')[:10] >= today
    )
    completed_pct = 0
    if stats['total']:
        completed_pct = round(100.0 * stats.get('completed', 0) / stats['total'])
    no_show_pct = 0
    if stats['total']:
        no_show_pct = round(100.0 * stats.get('no_show', 0) / stats['total'])
    stats['completed_pct'] = completed_pct
    stats['no_show_pct'] = no_show_pct

    agents = db.execute(
        "SELECT id, full_name FROM users WHERE role IN ('agent','admin') AND is_active = 1 ORDER BY full_name"
    ).fetchall()
    clients, properties, agents_list = _form_lists(db)

    # Serialize for calendar JS (dd/mm/yy display handled client-side)
    import json
    appt_json = json.dumps(all_rows, default=str)

    toast = request.args.get('toast', '').strip()

    return render_template(
        'admin_appointments.html',
        appointments=rows,
        all_appointments_json=appt_json,
        stats=stats,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
        appt_types=APPT_TYPES,
        appt_type_labels=APPT_TYPE_LABELS,
        agents=agents,
        clients=clients,
        properties=properties,
        q=q,
        status_filter=status,
        agent_id=agent_id,
        date_from=date_from,
        date_to=date_to,
        toast=toast,
    )


@admin.route('/admin/appointments/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_appointment():
    db = get_db()
    migrate_db()
    clients, properties, agents = _form_lists(db)

    if request.method == 'POST':
        data = _req_data()
        client_id = _req_get(data, 'client_id') or None
        property_id = _req_get(data, 'property_id') or None
        agent_id = _req_get(data, 'agent_id') or None
        appointment_date = str(_req_get(data, 'appointment_date', '')).strip()
        if not appointment_date:
            appt_date = str(_req_get(data, 'appt_date', '')).strip()
            appt_time = str(_req_get(data, 'appt_time', '10:00')).strip() or '10:00'
            if appt_date:
                appointment_date = appt_date + 'T' + appt_time
        notes = str(_req_get(data, 'notes', '')).strip()
        status = _normalize_status(_req_get(data, 'status', 'scheduled'))

        if not appointment_date:
            if _wants_json():
                return jsonify({'ok': False, 'error': 'Date and time are required.'}), 400
            flash('Date and time are required.', 'danger')
            return redirect(url_for('admin.admin_add_appointment'))

        try:
            client_id = int(client_id) if client_id not in (None, '') else None
        except (TypeError, ValueError):
            client_id = None
        try:
            property_id = int(property_id) if property_id not in (None, '') else None
        except (TypeError, ValueError):
            property_id = None
        try:
            agent_id = int(agent_id) if agent_id not in (None, '') else None
        except (TypeError, ValueError):
            agent_id = None

        appt_type = _normalize_type(_req_get(data, 'appointment_type') or _req_get(data, 'type') or 'meeting')
        try:
            duration = int(_req_get(data, 'duration_minutes') or 60)
        except (TypeError, ValueError):
            duration = 60
        appointment_date = appointment_date.replace(' ', 'T')
        if len(appointment_date) == 16:
            appointment_date = appointment_date + ':00'
        try:
            cur = db.execute(
                'INSERT INTO appointments (client_id, property_id, agent_id, appointment_date, status, notes, appointment_type, duration_minutes) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (client_id, property_id, agent_id, appointment_date, status, notes, appt_type, duration),
            )
        except Exception:
            cur = db.execute(
                'INSERT INTO appointments (client_id, property_id, agent_id, appointment_date, status, notes) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (client_id, property_id, agent_id, appointment_date, status, notes),
            )
        db.commit()
        aid = cur.lastrowid
        _log_appt_activity(db, aid, 'Created appointment', appointment_date)
        if _wants_json():
            rows = _appointments_query(db)
            match = next((r for r in rows if r['id'] == aid), None)
            return jsonify({'ok': True, 'id': aid, 'appointment': match})
        return redirect(url_for('admin.admin_appointments', toast='Scheduled'))

    return render_template(
        'admin_add_appointment.html',
        clients=clients,
        properties=properties,
        agents=agents,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
    )


@admin.route('/admin/appointments/<int:appointment_id>/edit', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_edit_appointment(appointment_id):
    db = get_db()
    migrate_db()
    row = db.execute(
        'SELECT * FROM appointments WHERE id = ?', (appointment_id,)
    ).fetchone()
    if not row:
        flash('Appointment not found.', 'warning')
        return redirect(url_for('admin.admin_appointments'))

    clients, properties, agents = _form_lists(db)

    if request.method == 'POST':
        data = _req_data()
        client_id = _req_get(data, 'client_id') or None
        property_id = _req_get(data, 'property_id') or None
        agent_id = _req_get(data, 'agent_id') or None
        appointment_date = str(_req_get(data, 'appointment_date', '')).strip()
        notes = str(_req_get(data, 'notes', '')).strip()
        status = _normalize_status(_req_get(data, 'status', 'scheduled'))
        appt_type = _normalize_type(_req_get(data, 'appointment_type') or _req_get(data, 'type') or 'meeting')
        try:
            duration = int(_req_get(data, 'duration_minutes') or 60)
        except (TypeError, ValueError):
            duration = 60

        if not appointment_date:
            if _wants_json():
                return jsonify({'ok': False, 'error': 'Date and time are required.'}), 400
            flash('Date and time are required.', 'danger')
            return redirect(url_for('admin.admin_edit_appointment', appointment_id=appointment_id))

        try:
            client_id = int(client_id) if client_id not in (None, '') else None
        except (TypeError, ValueError):
            client_id = None
        try:
            property_id = int(property_id) if property_id not in (None, '') else None
        except (TypeError, ValueError):
            property_id = None
        try:
            agent_id = int(agent_id) if agent_id not in (None, '') else None
        except (TypeError, ValueError):
            agent_id = None

        appointment_date = appointment_date.replace(' ', 'T')
        if len(appointment_date) == 16:
            appointment_date = appointment_date + ':00'

        try:
            db.execute(
                'UPDATE appointments SET client_id=?, property_id=?, agent_id=?, '
                'appointment_date=?, status=?, notes=?, appointment_type=?, duration_minutes=? WHERE id=?',
                (client_id, property_id, agent_id, appointment_date, status, notes, appt_type, duration, appointment_id),
            )
        except Exception:
            db.execute(
                'UPDATE appointments SET client_id=?, property_id=?, agent_id=?, '
                'appointment_date=?, status=?, notes=? WHERE id=?',
                (client_id, property_id, agent_id, appointment_date, status, notes, appointment_id),
            )
        db.commit()
        _log_appt_activity(db, appointment_id, 'Updated appointment', appointment_date)
        if _wants_json():
            return jsonify({'ok': True, 'id': appointment_id})
        return redirect(url_for('admin.admin_appointments', toast='Updated'))

    appt = dict(row)
    # datetime-local needs YYYY-MM-DDTHH:MM
    dt = (appt.get('appointment_date') or '').replace(' ', 'T')
    if len(dt) >= 16:
        dt = dt[:16]
    appt['appointment_date_input'] = dt

    return render_template(
        'admin_edit_appointment.html',
        appointment=appt,
        clients=clients,
        properties=properties,
        agents=agents,
        statuses=STATUSES,
        status_labels=STATUS_LABELS,
    )


@admin.route('/admin/appointments/<int:appointment_id>/status', methods=['POST'])
@login_required
@role_required('admin')
def admin_appointment_status(appointment_id):
    db = get_db()
    migrate_db()
    data = _req_data()
    status = _normalize_status(_req_get(data, 'status'))
    minutes = str(_req_get(data, 'minutes_of_meeting') or '').strip()
    row = db.execute(
        'SELECT id FROM appointments WHERE id = ?', (appointment_id,)
    ).fetchone()
    if not row:
        flash('Appointment not found.', 'warning')
        return redirect(url_for('admin.admin_appointments'))
    if status == 'completed' and minutes:
        db.execute(
            'UPDATE appointments SET status = ?, minutes_of_meeting = ? WHERE id = ?',
            (status, minutes, appointment_id),
        )
    else:
        db.execute(
            'UPDATE appointments SET status = ? WHERE id = ?',
            (status, appointment_id),
        )
    db.commit()
    _log_appt_activity(db, appointment_id, 'Status → ' + status, status)
    if _wants_json():
        return jsonify({'ok': True, 'id': appointment_id, 'status': status})
    return redirect(url_for('admin.admin_appointments', toast='Updated'))


@admin.route('/admin/appointments/<int:appointment_id>/minutes', methods=['POST'])
@login_required
@role_required('admin')
def admin_appointment_minutes(appointment_id):
    """Save Minutes of Meeting and mark appointment completed."""
    db = get_db()
    migrate_db()
    minutes = (request.form.get('minutes_of_meeting') or '').strip()
    row = db.execute(
        'SELECT id FROM appointments WHERE id = ?', (appointment_id,)
    ).fetchone()
    if not row:
        flash('Appointment not found.', 'warning')
        return redirect(url_for('admin.admin_appointments'))
    db.execute(
        'UPDATE appointments SET status = ?, minutes_of_meeting = ? WHERE id = ?',
        ('completed', minutes, appointment_id),
    )
    db.commit()
    log_activity('Minutes of meeting saved', 'appointment', appointment_id, 'completed')
    return redirect(url_for('admin.admin_appointments', toast='Updated'))


@admin.route('/admin/appointments/<int:appointment_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_appointment(appointment_id):
    db = get_db()
    migrate_db()
    row = db.execute(
        'SELECT id, appointment_date FROM appointments WHERE id = ?',
        (appointment_id,),
    ).fetchone()
    if not row:
        flash('Appointment not found.', 'warning')
        return redirect(url_for('admin.admin_appointments'))
    try:
        db.execute('DELETE FROM appointment_activity WHERE appointment_id = ?', (appointment_id,))
    except Exception:
        pass
    try:
        db.execute('DELETE FROM appointment_activity WHERE appointment_id = ?', (appointment_id,))
    except Exception:
        pass
    db.execute('DELETE FROM appointments WHERE id = ?', (appointment_id,))
    db.commit()
    log_activity('Deleted appointment', 'appointment', appointment_id, row['appointment_date'])
    if _wants_json():
        return jsonify({'ok': True})
    return redirect(url_for('admin.admin_appointments', toast='Deleted'))


@admin.route('/admin/appointments/<int:appointment_id>/reschedule', methods=['POST'])
@login_required
@role_required('admin')
def admin_appointment_reschedule(appointment_id):
    """Move appointment to a new date/time (drag and drop)."""
    db = get_db()
    migrate_db()
    data = request.get_json(silent=True) or {}
    new_dt = (data.get('appointment_date') or request.form.get('appointment_date') or '').strip()
    if not new_dt:
        return jsonify({'ok': False, 'error': 'appointment_date required'}), 400
    row = db.execute('SELECT id FROM appointments WHERE id = ?', (appointment_id,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    # Normalize T separator
    new_dt = new_dt.replace(' ', 'T')
    if len(new_dt) == 16:
        new_dt = new_dt + ':00'
    db.execute(
        'UPDATE appointments SET appointment_date = ? WHERE id = ?',
        (new_dt, appointment_id),
    )
    db.commit()
    _log_appt_activity(db, appointment_id, 'Rescheduled', new_dt)
    return jsonify({'ok': True, 'appointment_date': new_dt})

@admin.route('/admin/appointments/api/list')
@login_required
@role_required('admin')
def admin_appointments_api_list():
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    if status:
        status = _normalize_status(status)
        if status not in STATUSES:
            status = ''
    agent_id = request.args.get('agent_id', '').strip()
    rows = _appointments_query(db, q=q, status=status, agent_id=agent_id)
    return jsonify({'ok': True, 'appointments': rows, 'count': len(rows)})


@admin.route('/admin/appointments/api/availability')
@login_required
@role_required('admin')
def admin_appointments_availability():
    db = get_db()
    migrate_db()
    day = (request.args.get('date') or '').strip()[:10]
    agent_id = request.args.get('agent_id', '').strip()
    if not day:
        return jsonify({'ok': False, 'error': 'date required'}), 400
    sql = (
        "SELECT a.id, a.appointment_date, a.agent_id, a.status, c.full_name as client_name "
        "FROM appointments a LEFT JOIN clients c ON a.client_id = c.id "
        "WHERE date(a.appointment_date) = date(?) AND lower(IFNULL(a.status,'')) = 'scheduled'"
    )
    params = [day]
    if agent_id and agent_id.isdigit():
        sql += " AND a.agent_id = ?"
        params.append(int(agent_id))
    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    occupied = set()
    for r in rows:
        s = str(r.get('appointment_date') or '')
        if 'T' in s or ' ' in s:
            part = s.replace(' ', 'T').split('T')[1][:2]
            try:
                occupied.add(int(part))
            except ValueError:
                pass
    free = [h for h in range(8, 18) if h not in occupied]
    return jsonify({'ok': True, 'date': day, 'occupied_hours': sorted(occupied), 'free_hours': free, 'appointments': rows})


@admin.route('/admin/appointments/api/<int:appointment_id>')
@login_required
@role_required('admin')
def admin_appointment_api_detail(appointment_id):
    db = get_db()
    migrate_db()
    row = db.execute(
        "SELECT a.*, c.full_name as client_name, p.title as property_title, u.full_name as agent_name "
        "FROM appointments a "
        "LEFT JOIN clients c ON a.client_id = c.id "
        "LEFT JOIN properties p ON a.property_id = p.id "
        "LEFT JOIN users u ON a.agent_id = u.id WHERE a.id = ?",
        (appointment_id,),
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'not found'}), 404
    appt = dict(row)
    appt['status'] = _normalize_status(appt.get('status'))
    appt['appointment_type'] = _normalize_type(appt.get('appointment_type') or 'meeting')
    activity = []
    try:
        activity = [dict(r) for r in db.execute(
            "SELECT aa.*, u.full_name as user_name FROM appointment_activity aa "
            "LEFT JOIN users u ON aa.user_id = u.id WHERE aa.appointment_id = ? "
            "ORDER BY aa.created_at DESC LIMIT 50", (appointment_id,)
        ).fetchall()]
    except Exception:
        pass
    return jsonify({'ok': True, 'appointment': appt, 'activity': activity})
