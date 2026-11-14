"""Admin tasks routes."""

from flask import (
    render_template, request, redirect, url_for, session, flash, jsonify,
    current_app, make_response
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
import json
import csv
import io

from core.db import get_db, migrate_db
from core.helpers import (
    login_required, role_required, get_setting, set_setting,
    format_money, log_activity, get_redirect_for_role
)
from routes.admin import admin


@admin.route('/admin/tasks')
@login_required
@role_required('admin')
def admin_tasks():
    db = get_db()
    migrate_db()
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    priority = request.args.get('priority', '').strip()
    sql = (
        "SELECT t.*, u.full_name as assignee_name, c.full_name as client_name, p.title as property_title "
        "FROM tasks t "
        "LEFT JOIN users u ON t.assigned_to = u.id "
        "LEFT JOIN clients c ON t.related_client_id = c.id "
        "LEFT JOIN properties p ON t.related_property_id = p.id WHERE 1=1"
    )
    params = []
    if q:
        sql += " AND (IFNULL(t.title,'') LIKE ? OR IFNULL(t.description,'') LIKE ? OR IFNULL(u.full_name,'') LIKE ?)"
        qq = '%' + q + '%'
        params.extend([qq, qq, qq])
    if status in ('pending', 'in_progress', 'done', 'cancelled'):
        sql += " AND t.status = ?"
        params.append(status)
    if priority in ('low', 'medium', 'high', 'urgent'):
        sql += " AND t.priority = ?"
        params.append(priority)
    sql += " ORDER BY CASE t.status WHEN 'pending' THEN 0 WHEN 'in_progress' THEN 1 WHEN 'done' THEN 2 ELSE 3 END, t.due_date IS NULL, t.due_date, t.created_at DESC"
    tasks = [dict(r) for r in db.execute(sql, params).fetchall()]
    all_tasks = [dict(r) for r in db.execute(
        "SELECT status, priority FROM tasks"
    ).fetchall()]
    stats = {
        'total': len(all_tasks),
        'pending': sum(1 for t in all_tasks if t.get('status') == 'pending'),
        'in_progress': sum(1 for t in all_tasks if t.get('status') == 'in_progress'),
        'done': sum(1 for t in all_tasks if t.get('status') == 'done'),
        'cancelled': sum(1 for t in all_tasks if t.get('status') == 'cancelled'),
        'high': sum(1 for t in all_tasks if t.get('priority') in ('high', 'urgent')),
    }
    toast = request.args.get('toast', '').strip()
    return render_template(
        'admin_tasks.html',
        tasks=tasks,
        stats=stats,
        q=q,
        status_filter=status,
        priority_filter=priority,
        toast=toast,
    )


@admin.route('/admin/tasks/add', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_add_task():
    db = get_db()
    agents = db.execute("SELECT id, full_name FROM users WHERE role IN ('admin','agent') AND is_active=1 ORDER BY full_name").fetchall()
    clients = db.execute('SELECT id, full_name FROM clients ORDER BY full_name').fetchall()
    properties = db.execute('SELECT id, title FROM properties ORDER BY title').fetchall()
    if request.method == 'POST':
        data = {}
        if request.is_json:
            data = request.get_json(silent=True) or {}
        else:
            data = request.form
        title = (data.get('title') or '').strip()
        wants_json = (
            request.is_json
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in (request.headers.get('Accept') or '')
        )
        if not title:
            if wants_json:
                return jsonify({'ok': False, 'error': 'Title required.'}), 400
            flash('Title required.', 'danger')
            return redirect(url_for('admin.admin_add_task'))
        assigned = data.get('assigned_to') or None
        client_id = data.get('related_client_id') or None
        prop_id = data.get('related_property_id') or None
        description = data.get('description') or ''
        due_date = data.get('due_date') or None
        status = data.get('status') or 'pending'
        priority = data.get('priority') or 'medium'
        try:
            assigned_i = int(assigned) if assigned not in (None, '') else None
        except (TypeError, ValueError):
            assigned_i = None
        try:
            client_i = int(client_id) if client_id not in (None, '') else None
        except (TypeError, ValueError):
            client_i = None
        try:
            prop_i = int(prop_id) if prop_id not in (None, '') else None
        except (TypeError, ValueError):
            prop_i = None
        cur = db.execute(
            "INSERT INTO tasks (title, description, due_date, status, priority, assigned_to, related_client_id, related_property_id, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
            (title, description, due_date, status, priority, assigned_i, client_i, prop_i, session['user_id'])
        )
        db.commit()
        log_activity('Created task', 'task', cur.lastrowid, title)
        if wants_json:
            return jsonify({'ok': True, 'id': cur.lastrowid})
        nxt = (data.get('next') or request.args.get('next') or '').strip()
        if nxt == 'dashboard':
            return redirect(url_for('admin.admin_dashboard'))
        return redirect(url_for('admin.admin_tasks', toast='Updated'))
    return render_template('admin_add_task.html', agents=agents, clients=clients, properties=properties)


@admin.route('/admin/tasks/status/<int:task_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_task_status(task_id):
    status = request.form.get('status', 'pending')
    if status not in ('pending', 'in_progress', 'done', 'cancelled'):
        return redirect(url_for('admin.admin_tasks'))
    db = get_db()
    task = db.execute('SELECT title FROM tasks WHERE id=?', (task_id,)).fetchone()
    db.execute('UPDATE tasks SET status=? WHERE id=?', (status, task_id))
    db.commit()
    if task:
        log_activity('Updated task', 'task', task_id, f"{task['title']} → {status}")
    if request.form.get('next') == 'dashboard':
        return redirect(url_for('admin.admin_dashboard'))
    return redirect(url_for('admin.admin_tasks', toast='Updated'))


@admin.route('/admin/tasks/delete/<int:task_id>', methods=['POST'])
@login_required
@role_required('admin')
def admin_delete_task(task_id):
    db = get_db()
    db.execute('DELETE FROM tasks WHERE id=?', (task_id,))
    db.commit()
    return redirect(url_for('admin.admin_tasks', toast='Updated'))


