import os
import uuid
import logging
import sqlite3
from flask import Flask, request, render_template, redirect, url_for, session
from dotenv import load_dotenv
from twilio.twiml.messaging_response import MessagingResponse

from twilio_utils import download_audio_file, send_whatsapp_message
from transcription_utils import transcribe_audio
from sensitive_utils.detector import detect_and_encrypt_sensitive
from db_utils import (
    get_agent_and_customers,
    get_customer_history,
    log_agent_notes,
    get_all_data_for_agent,
    get_full_case_details,
    create_communication_record,
    get_pending_reports_for_supervisor,
    submit_supervisor_decision
)
from nlu_utils import get_intent_and_entities
from llm_utils import generate_priority_plan, generate_summary_for_supervisor

load_dotenv()
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET", "secret-key")
conversation_state = {}

# ------------------ WhatsApp Agent Interface ------------------

@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    resp = MessagingResponse()
    from_number = request.form.get("From")
    incoming_msg = request.form.get("Body", "").strip()
    user_context = conversation_state.get(from_number, {})

    try:
        # Handle voice notes
        if int(request.form.get("NumMedia", 0)) > 0:
            media_url = request.form.get("MediaUrl0")
            temp_audio_path = f"temp_{uuid.uuid4().hex}.ogg"
            try:
                audio_data = download_audio_file(media_url)
                with open(temp_audio_path, "wb") as f:
                    f.write(audio_data)
                transcribed_text = transcribe_audio(temp_audio_path)
                masked_text = detect_and_encrypt_sensitive(transcribed_text)
                resp.message(f"🗣 Transcribed text:\n\n{masked_text}")
            finally:
                if os.path.exists(temp_audio_path):
                    os.remove(temp_audio_path)
            return str(resp)

        # Handle text messages
        intent, account_number = get_intent_and_entities(incoming_msg)

        if not intent:
            resp.message("Sorry, I didn't understand that. Try commands like 'list', 'history <account>', 'log for <account>: <reason>'.")
            return str(resp)

        if intent == "get_customer_list":
            data = get_all_data_for_agent(from_number)
            message = "\n".join([f"{r['account_number']}: {r['customer_name']}" for r in data])
            resp.message("📋 Customers:\n" + message if message else "No customers found.")
        
        elif intent == "get_customer_history" and account_number:
            history = get_customer_history(account_number)
            if not history:
                resp.message(f"No history found for {account_number}")
            else:
                history_str = "\n\n".join([f"{h['timestamp']}: {h['notes']}" for h in history])
                resp.message(f"📖 History for {account_number}:\n\n{history_str}")

        elif intent == "log_reason" and account_number:
            reason = incoming_msg.split(":", 1)[-1].strip()
            log_agent_notes(from_number, account_number, reason)
            resp.message(f"📝 Logged visit for {account_number}: {reason}")

        elif intent == "get_due_amount" and account_number:
            details = get_full_case_details(account_number)
            amount_due = details.get("due_amount", "N/A")
            resp.message(f"💰 Amount due for {account_number}: {amount_due}")

        elif intent == "get_summary" and account_number:
            details = get_full_case_details(account_number)
            summary = generate_summary_for_supervisor(details)
            resp.message(f"🧾 Summary for {account_number}:\n\n{summary}")

        elif intent == "send_report" and account_number:
            agent_data = get_full_case_details(account_number)
            summary = generate_summary_for_supervisor(agent_data)
            comm_id = create_communication_record(account_number, summary)
            resp.message(f"📤 Report sent for review.\nID: {comm_id}")

        elif intent == "generate_plan":
            data = get_all_data_for_agent(from_number)
            plan = generate_priority_plan(data)
            resp.message(f"📌 Priority Plan:\n{plan}")

        else:
            resp.message("❓ Unrecognized or incomplete command.")
    except Exception as e:
        logging.exception("Error processing WhatsApp message")
        resp.message("An error occurred. Please try again.")

    return str(resp)

# ------------------ Supervisor Web Interface ------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form["phone"]
        password = request.form["password"]
        if phone == os.getenv("SUPERVISOR_PHONE") and password == os.getenv("SUPERVISOR_PASS"):
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid credentials.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard", methods=["GET"])
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    query = request.args.get("query", "")
    sort_by = request.args.get("sort_by", "account_number")
    reports = get_pending_reports_for_supervisor(query, sort_by)
    return render_template("dashboard.html", reports=reports, query=query, sort_by=sort_by)

@app.route("/submit_decision/<comm_id>", methods=["POST"])
def submit_decision(comm_id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    decision = request.form["decision"]
    submit_supervisor_decision(comm_id, decision)
    return redirect(url_for("dashboard"))

# ------------------ App Entry Point ------------------

if __name__ == "__main__":
    app.run(port=5000)
