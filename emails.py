import os
import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from dotenv import load_dotenv

load_dotenv()  # reads the settings in the .env file


def send_confirmation_email(to: str, confirm_token: str, leave_token: str):
    site = os.getenv("SITE_URL", "http://localhost:8443")
    whatsapp = os.getenv("WHATSAPP_URL", "")
    confirm_link = f"{site}/api/waitlist/confirm?token={confirm_token}"
    leave_link = f"{site}/api/waitlist/leave?token={leave_token}"

    text = f"""Hello, thank you for joining the Medge waitlist.

Please confirm your email so we can reach you when beta invitations open.

Confirm my email: {confirm_link}

Want to hear first? Join our WhatsApp channel: {whatsapp}

If this was not you, ignore this email and nothing will happen.

Leave the waitlist: {leave_link}

Medge is a product of KytoLabx.
"""

    html = f"""<div style="max-width:560px;margin:auto;padding:24px;font-family:Georgia,serif">
<p>Hello, thank you for joining the Medge waitlist.</p>
<p>Please confirm your email so we can reach you when beta invitations open.</p>
<p><a href="{escape(confirm_link)}" style="display:inline-block;padding:14px 24px;background:#0f766e;color:#ffffff;border-radius:12px;text-decoration:none">Confirm my email</a></p>
<p>Want to hear first? <a href="{escape(whatsapp)}">Join our WhatsApp channel</a>.</p>
<p>If this was not you, ignore this email and nothing will happen.</p>
<p style="font-size:14px"><a href="{escape(leave_link)}">Leave the waitlist</a><br>Medge is a product of KytoLabx.</p>
</div>"""

    # Practice mode: with no email server set up, print the email instead
    host = os.getenv("SMTP_HOST")
    if not host:
        print(f"\n--- PRACTICE EMAIL to {to} ---\n{text}--- END ---\n", flush=True)
        return

    message = EmailMessage()
    message["Subject"] = "Confirm your email for the Medge beta"
    message["From"] = os.getenv("MAIL_FROM", "Medge <hello@medge.ng>")
    message["To"] = to
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    port = int(os.getenv("SMTP_PORT", "587"))
    context = ssl.create_default_context()  # used to encrypt the connection

    # Port 465 is encrypted from the start. Port 587 switches encryption on after connecting
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=20)

    with server:
        if port != 465:
            server.starttls(context=context)
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        server.send_message(message)