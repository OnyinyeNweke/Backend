import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError
from sqlmodel import Field, Session, SQLModel, create_engine, select

from emails import send_confirmation_email


# 1. Database: a single file called medge.db
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///medge.db")
engine = create_engine(DATABASE_URL)
# engine = create_engine("sqlite:///medge.db")


# 2. The table: one row per person on the waitlist
class Signup(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    consent_text: str
    consent_version: str
    referral_source: str
    submission_token: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Optional answers (empty until the person fills them in)
    first_name: str | None = None
    phone_type: str | None = None
    location: str | None = None
    use_for: str | None = None
    # Email confirmation and leaving
    confirm_token: str = Field(unique=True, index=True)
    leave_token: str = Field(unique=True, index=True)
    confirmed_at: datetime | None = None


# People who left. We keep only a scrambled (hashed) copy of the email
class OptOut(SQLModel, table=True):
    email_hash: str = Field(primary_key=True)
    left_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def hash_email(email: str) -> str:
    return hashlib.sha256(email.encode()).hexdigest()


# 3. What the frontend sends us (names must match the frontend exactly)
class JoinRequest(BaseModel):
    email: EmailStr
    consentText: str
    consentVersion: str
    referralSource: Literal["hero", "final"]
    website: str = ""  # bot trap: real people never see this field


# 4. What we send back
class JoinResponse(BaseModel):
    status: Literal["created", "duplicate"]
    submissionToken: str | None = None


# 5. Create the table when the app starts
@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(lifespan=lifespan)


# 6. The endpoint
@app.post("/api/waitlist", response_model=JoinResponse, response_model_exclude_none=True)
def join_waitlist(data: JoinRequest, background_tasks: BackgroundTasks):
    token = secrets.token_urlsafe(32)

    # A filled bot trap looks successful, but nothing is saved
    if data.website:
        return JoinResponse(status="created", submissionToken=token)

    email = data.email.lower()
    confirm_token = secrets.token_urlsafe(32)
    leave_token = secrets.token_urlsafe(32)

    signup = Signup(
        email=email,
        confirm_token=confirm_token,
        leave_token=leave_token,
        consent_text=data.consentText,
        consent_version=data.consentVersion,
        referral_source=data.referralSource,
        submission_token=token,
    )

    with Session(engine) as session:
        session.add(signup)
        try:
            session.commit()
        except IntegrityError:  # the email is already in the table
            return JoinResponse(status="duplicate")

    # Send the email after replying, so the website does not have to wait
    background_tasks.add_task(send_confirmation_email, email, confirm_token, leave_token)

    return JoinResponse(status="created", submissionToken=token)


# 7. Optional profile answers
class ProfileRequest(BaseModel):
    submissionToken: str
    field: Literal["firstName", "phoneType", "location", "useFor"]
    value: str


# Frontend field name -> column name in our table
COLUMNS = {
    "firstName": "first_name",
    "phoneType": "phone_type",
    "location": "location",
    "useFor": "use_for",
}

# The only answers allowed for the tap-to-choose questions
OPTIONS = {
    "phoneType": ["Android", "iPhone", "Other", "Not sure"],
    "location": [
        "Lagos",
        "Abuja",
        "Port Harcourt",
        "Ibadan",
        "Another city in Nigeria",
        "Outside Nigeria",
    ],
    "useFor": ["Me", "My household", "A relative", "I work in healthcare"],
}


@app.patch("/api/waitlist/profile")
def save_profile(data: ProfileRequest):
    value = data.value.strip()

    if data.field == "firstName":
        if not 1 <= len(value) <= 50:
            raise HTTPException(status_code=422, detail="Name must be 1 to 50 characters")
    elif value not in OPTIONS[data.field] + ["Prefer not to say"]:
        raise HTTPException(status_code=422, detail="That is not one of the options")

    with Session(engine) as session:
        query = select(Signup).where(Signup.submission_token == data.submissionToken)
        signup = session.exec(query).first()
        if signup is None:
            raise HTTPException(status_code=404, detail="Signup not found")

        setattr(signup, COLUMNS[data.field], value)
        session.add(signup)
        session.commit()

    return {"status": "saved"}


# 8. The "Confirm my email" link
@app.get("/api/waitlist/confirm")
def confirm_email(token: str):
    with Session(engine) as session:
        signup = session.exec(select(Signup).where(Signup.confirm_token == token)).first()
        if signup is None:
            raise HTTPException(status_code=404, detail="This link is not valid")

        if signup.confirmed_at is None:
            signup.confirmed_at = datetime.now(timezone.utc)
            session.add(signup)

        # If they left before and have now joined again, remove the old record
        old = session.get(OptOut, hash_email(signup.email))
        if old:
            session.delete(old)
        session.commit()

    site = os.getenv("SITE_URL", "http://localhost:8443")
    return RedirectResponse(f"{site}/email-confirmed", status_code=303)


# 9. Leaving: opening the link only shows a button. Nothing is deleted yet,
# because some email apps open links automatically to scan them
@app.get("/api/waitlist/leave", response_class=HTMLResponse)
def leave_page():
    return """<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Leave the Medge waitlist</title>
<h1>Leave the waitlist?</h1>
<p>We will delete your details.</p>
<form method="post"><button type="submit">Yes, remove me</button></form>
"""


# 10. Pressing the button deletes the person
@app.post("/api/waitlist/leave", response_class=HTMLResponse)
def leave_waitlist(token: str):
    with Session(engine) as session:
        signup = session.exec(select(Signup).where(Signup.leave_token == token)).first()
        if signup is None:
            raise HTTPException(status_code=404, detail="This link is not valid or was already used")

        email_hash = hash_email(signup.email)
        if session.get(OptOut, email_hash) is None:
            session.add(OptOut(email_hash=email_hash))
        session.delete(signup)
        session.commit()

    return """<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>You have left</title>
<h1>You have left the waitlist.</h1>
<p>Your details have been deleted.</p>
"""