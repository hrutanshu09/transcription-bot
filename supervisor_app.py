from flask import Flask, render_template, request, redirect, url_for, session
from db_utils import get_pending_reports_for_supervisor, submit_supervisor_decision
import sqlite3
import bcrypt
import os

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supervisor-secret")

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form["phone"]
        password = request.form["password"].encode("utf-8")

        conn = sqlite3.connect("loan_recovery.db")
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM supervisors WHERE whatsapp_number = ?", (phone,))
        user = cursor.fetchone()
        conn.close()

        if user and bcrypt.checkpw(password, user[0].encode("utf-8")):
            session["supervisor_number"] = phone
            return redirect(url_for("pending_reports"))
        else:
            return render_template("login.html", error="Invalid credentials.")
    return render_template("login.html")

@app.route("/dashboard")
def pending_reports():
    if "supervisor_number" not in session:
        return redirect(url_for("login"))
    
    supervisor_number = session["supervisor_number"]
    reports = get_pending_reports_for_supervisor(supervisor_number)
    
    query = request.args.get("query", "").lower()
    sort_by = request.args.get("sort_by", "account_number")

    if query:
        reports = [r for r in reports if query in r['account_number'].lower() or query in r['summary_report'].lower()]
    
    if sort_by == "account_number":
        reports = sorted(reports, key=lambda r: r['account_number'])
    elif sort_by == "comm_id":
        reports = sorted(reports, key=lambda r: r['comm_id'])

    return render_template("dashboard.html", reports=reports, query=query, sort_by=sort_by)

@app.route("/decision/<int:comm_id>", methods=["POST"])
def submit_decision(comm_id):
    if "supervisor_number" not in session:
        return redirect(url_for("login"))

    decision = request.form["decision"]
    agent, account = submit_supervisor_decision(comm_id, decision)

    if agent:
        return redirect(url_for("pending_reports"))
    else:
        return f"Failed to record decision for report ID {comm_id}. Please try again."

@app.route("/logout")
def logout():
    session.pop("supervisor_number", None)
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)
