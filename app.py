import streamlit as st
import datetime
import gspread
import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from pytz import timezone
import json

# --- CONFIG ---
SHEET_ID = st.secrets["SHEET_ID"]
SHEET_NAME = st.secrets["SHEET_NAME"]
CALENDAR_ID = st.secrets["CALENDAR_ID"]

# --- AUTH ---
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

# Instead of a local credentials.json file, use the json string from secrets
creds_json = st.secrets["GCP_CREDENTIALS_JSON"]
creds_dict = json.loads(creds_json)

creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)
calendar_service = build("calendar", "v3", credentials=creds)

tz = timezone("Australia/Brisbane")

# --- SESSION STATE ---
if "mode" not in st.session_state:
    st.session_state.mode = "search"

if "po_counter" not in st.session_state:
    st.session_state.po_counter = len(sheet.get_all_records()) + 1

if "current_customer" not in st.session_state:
    st.session_state.current_customer = None

if "show_cancel_confirm" not in st.session_state:
    st.session_state.show_cancel_confirm = False

def generate_po_number():
    return f"E{str(st.session_state.po_counter).zfill(6)}"

def get_calendar_events(date):
    time_min = tz.localize(datetime.datetime.combine(date, datetime.time(7, 0)))
    time_max = tz.localize(datetime.datetime.combine(date, datetime.time(18, 0)))

    events_result = calendar_service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()
    return events_result.get("items", [])

def is_time_available(date, start_time):
    start_dt = tz.localize(datetime.datetime.combine(date, start_time))
    end_dt = start_dt + datetime.timedelta(hours=4)
    events = get_calendar_events(date)

    for event in events:
        event_start = event["start"].get("dateTime")
        event_end = event["end"].get("dateTime")
        if event_start and event_end:
            estart = datetime.datetime.fromisoformat(event_start).astimezone(tz)
            eend = datetime.datetime.fromisoformat(event_end).astimezone(tz)
            if not (end_dt <= estart or start_dt >= eend):
                return False
    return True

def get_available_slots(date):
    slots = []
    for hour in range(7, 15):  # From 7 AM to 2 PM (4-hour blocks until 6 PM)
        start = datetime.time(hour, 0)
        if is_time_available(date, start):
            slots.append(start)
    return slots

def create_calendar_event(po, name, phone, from_addr, to_addr, date, start, end, service, notes):
    event = {
        "summary": f"{po} – {name} – {service}",
        "description": f"Phone: {phone}\nFrom: {from_addr}\nTo: {to_addr}\nNotes: {notes}",
        "start": {"dateTime": tz.localize(datetime.datetime.combine(date, start)).isoformat()},
        "end": {"dateTime": tz.localize(datetime.datetime.combine(date, end)).isoformat()},
    }
    return calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()["id"]

def update_calendar_event(event_id, *args, **kwargs):
    try:
        calendar_service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    except Exception as e:
        st.warning(f"Could not delete old event: {e}")
    return create_calendar_event(*args, **kwargs)

def delete_calendar_event(event_id):
    try:
        calendar_service.events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
    except Exception as e:
        st.error(f"Delete failed: {e}")

def append_booking_to_sheet(values):
    sheet.append_row(values)

def update_booking_in_sheet(row_index, values):
    sheet.update(f"A{row_index}:K{row_index}", [values])

def find_customer(name="", phone=""):
    records = sheet.get_all_records()
    for i, row in enumerate(records, start=2):
        if name and name.lower() in str(row["Name"]).lower():
            return i, row
        if phone and str(row["Phone"])[-9:] == phone[-9:]:
            return i, row
    return None, None

def display_customer(cust):
    st.write(f"**PO No:** {cust['PO No']}")
    st.write(f"**Name:** {cust['Name']}")
    st.write(f"**Phone:** {cust['Phone']}")
    st.write(f"**From:** {cust['From Address']}")
    st.write(f"**To:** {cust['To Address']}")
    st.write(f"**Date:** {cust['Date']}")
    st.write(f"**Time:** {cust['Time']} – {cust['End Time']}")
    st.write(f"**Service:** {cust['Service']}")
    st.write(f"**Notes:** {cust['Notes']}")

# --- UI ---
st.title("📞The Moving Men")

# Search Page
if st.session_state.mode == "search":
    st.header("🔍 Search Customer")

    # --- Search Form ---
    with st.form("search_form"):
        name = st.text_input("Name")
        phone = st.text_input("Phone (last 9 digits okay)")
        submitted = st.form_submit_button("Search")
        if submitted:
            idx, cust = find_customer(name=name, phone=phone)
            if cust:
                st.session_state.current_customer = (idx, cust)
                st.session_state.mode = "view"
            else:
                st.warning("Not found. You can create a new booking.")

    if st.button("➕ New Booking"):
        st.session_state.mode = "new_booking"

    # --- Upcoming Appointments ---
    st.subheader("📅 Upcoming Bookings")
    df = pd.DataFrame(sheet.get_all_records())
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    today = pd.to_datetime(datetime.date.today())
    upcoming = df[df["Date"] >= today].sort_values("Date")

    for i, row in upcoming.iterrows():
        with st.expander(f"{row['Date'].date()} - {row['Name']} ({row['PO No']})"):
            st.write(f"📍 From: {row['From Address']}")
            st.write(f"📦 To: {row['To Address']}")
            st.write(f"⏰ {row['Time']} – {row['End Time']}")
            st.write(f"🚚 {row['Service']}")
            st.write(f"📝 {row['Notes']}")
            if st.button("🔍 View/Modify Booking", key=row["PO No"]):
                st.session_state.current_customer = (i + 2, row)  # +2 accounts for header and 0-index
                st.session_state.mode = "view"


# View Customer
elif st.session_state.mode == "view":
    idx, cust = st.session_state.current_customer
    st.header("👤 Customer Info")
    display_customer(cust)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🕓 Rebook"):
            st.session_state.mode = "rebook"
    with col2:
        if st.button("❌ Cancel Booking"):
            st.session_state.show_cancel_confirm = True

    if st.session_state.show_cancel_confirm:
        st.warning("Are you sure?")
        if st.button("Yes, Cancel"):
            if cust.get("Event ID"):
                delete_calendar_event(cust["Event ID"])
            sheet.update(f"A{idx}:K{idx}", [[""]*11])
            st.success("Cancelled.")
            st.session_state.mode = "search"
            st.session_state.current_customer = None
            st.session_state.show_cancel_confirm = False
        if st.button("No"):
            st.session_state.show_cancel_confirm = False

    if st.button("⬅️ Back"):
        st.session_state.mode = "search"
        st.session_state.current_customer = None

# Booking or Rebooking
elif st.session_state.mode in ("new_booking", "rebook"):
    st.header("🗓️ Booking Form")
    if st.button("⬅️ Return to Search"):
        st.session_state.mode = "search"
        st.session_state.current_customer = None
        st.stop()

    if st.session_state.mode == "rebook":
        idx, cust = st.session_state.current_customer
        po = cust["PO No"]
        name = cust["Name"]
        phone = cust["Phone"]
        from_addr = cust["From Address"]
        to_addr = cust["To Address"]
        date = pd.to_datetime(cust["Date"]).date()
        notes = cust["Notes"]
        service = cust["Service"]
        event_id = cust["Event ID"]
    else:
        po = generate_po_number()
        name = phone = from_addr = to_addr = notes = ""
        date = datetime.date.today()
        service = "Small Truck + 1 Man"
        event_id = ""
        idx = None

    name = st.text_input("Name", value=name)
    phone = st.text_input("Phone", value=phone)
    from_addr = st.text_input("From Address", value=from_addr)
    to_addr = st.text_input("To Address", value=to_addr)
    date = st.date_input("Moving Date", value=date)

    slots = get_available_slots(date)
    if not slots:
        st.warning("No 4-hour slots available between 7 AM and 6 PM.")
        st.stop()

    start_time = st.selectbox("Start Time", slots)
    end_time = (datetime.datetime.combine(datetime.date.today(), start_time) + datetime.timedelta(hours=4)).time()

    service = st.selectbox("Service", [
        "Small Truck + 1 Man",
        "Small Truck + 2 Men",
        "Big Truck + 1 Man",
        "Big Truck + 2 Men"
    ], index=["Small Truck + 1 Man", "Small Truck + 2 Men", "Big Truck + 1 Man", "Big Truck + 2 Men"].index(service))

    notes = st.text_area("Notes", value=notes)

    if st.button("Submit Booking"):
        if st.session_state.mode == "new_booking":
            event_id = create_calendar_event(po, name, phone, from_addr, to_addr, date, start_time, end_time, service, notes)
            append_booking_to_sheet([po, name, phone, from_addr, to_addr, str(date), str(start_time), str(end_time), service, notes, event_id])
            st.session_state.po_counter += 1
            st.success("Booking created.")
        else:
            event_id = update_calendar_event(event_id, po, name, phone, from_addr, to_addr, date, start_time, end_time, service, notes)
            update_booking_in_sheet(idx, [po, name, phone, from_addr, to_addr, str(date), str(start_time), str(end_time), service, notes, event_id])
            st.success("Booking updated.")

        st.session_state.mode = "search"
        st.session_state.current_customer = None
