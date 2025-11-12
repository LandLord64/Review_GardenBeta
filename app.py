import streamlit as st
import pandas as pd
from openai import OpenAI
import os
import io
import pickle
import base64
from datetime import datetime
from email.mime.text import MIMEText
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import gspread
from twilio.rest import Client
from dotenv import load_dotenv
import time
import re

# ================== LOAD ENV ==================
load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")
SHEET_ID = os.getenv("SHEET_ID")

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY and OPENAI_API_KEY != "not-set-yet" else None

GMAIL_CREDS = "credentials/gmail_credentials.json"
SERVICE_ACCOUNT = "credentials/service_account.json"
TOKEN_FILE = "token.pkl"

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

st.set_page_config(page_title="ReviewGarden", page_icon="🌿", layout="wide")
st.title("🌿 ReviewGarden - AI Review Booster")

# ================== INIT SERVICES ==================
@st.cache_resource
def init_twilio():
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return None

@st.cache_resource
def init_sheets():
    try:
        gc = gspread.service_account(filename=SERVICE_ACCOUNT)
        return gc.open_by_key(SHEET_ID)
    except Exception as e:
        return None

twilio_client = init_twilio()
spreadsheet = init_sheets()

# ================== SESSION STATE ==================
if "df_processed" not in st.session_state:
    st.session_state.df_processed = None

# ================== UTILITY FUNCTIONS ==================
def check_csv(df):
    required = ["Business Name","Customer Name","Email","Phone","Service Date","Review Link"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Missing required columns: {', '.join(missing)}")
        return False
    try:
        df["Service Date"] = pd.to_datetime(df["Service Date"])
    except:
        st.error("Invalid date format in Service Date")
        return False
    return True

def generate_ai_message(business_name, customer_name, service_type=""):
    if not client:
        return f"Hi {customer_name}! Hope you enjoyed your experience at {business_name}. Please leave us a review!"
    
    prompt = f"Write a friendly SMS under 150 chars asking {customer_name} to leave a Google review for {business_name}. Warm, casual tone."
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a friendly local business owner."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Hi {customer_name}! Hope you enjoyed your experience at {business_name}. Please leave us a review!"

def gmail_auth():
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDS, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
    return build("gmail", "v1", credentials=creds)

def send_sms(df):
    if not twilio_client:
        st.error("Twilio not configured")
        return df, 0, len(df)
        
    sent, failed = 0, 0
    progress = st.progress(0)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        try:
            message = f"{row['Generated_Message']} {row['Review Link']} Reply STOP to opt out."
            twilio_client.messages.create(
                body=message,
                from_=TWILIO_PHONE,
                to=str(row["Phone"]).strip()
            )
            df.at[i, "SMS_Status"] = "✅"
            sent += 1
        except Exception as e:
            df.at[i, "SMS_Status"] = "❌"
            df.at[i, "Error"] = str(e)
            failed += 1
        
        progress.progress((i + 1) / len(df))
        status_text.text(f"Progress {i + 1}/{len(df)} | ✅ {sent} | ❌ {failed}")
    
    return df, sent, failed

def send_email(df, subject=None):
    try:
        service = gmail_auth()
    except Exception as e:
        st.error(f"Gmail authentication failed: {e}")
        return df, 0, len(df)
    
    sent, failed = 0, 0
    progress = st.progress(0)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        try:
            body = f"""Hi {row['Customer Name']},

{row['Generated_Message']}

Leave your review here: {row['Review Link']}

Thank you,
{row['Business Name']} Team
"""
            message = MIMEText(body)
            message['to'] = row["Email"]
            message['subject'] = subject or f"Share your experience with {row['Business Name']}"
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            service.users().messages().send(userId="me", body={"raw": raw}).execute()
            df.at[i, "Email_Status"] = "✅"
            sent += 1
        except Exception as e:
            df.at[i, "Email_Status"] = "❌"
            df.at[i, "Error"] = str(e)
            failed += 1
        
        progress.progress((i + 1) / len(df))
        status_text.text(f"Progress {i + 1}/{len(df)} | ✅ {sent} | ❌ {failed}")
    
    return df, sent, failed

def generate_messages_batch(df):
    messages = []
    progress_bar = st.progress(0)
    
    for idx, row in df.iterrows():
        message = generate_ai_message(
            row["Business Name"],
            row["Customer Name"], 
            row.get("Service Type", "")
        )
        messages.append(message)
        progress_bar.progress((idx + 1) / len(df))
    
    df["Generated_Message"] = messages
    return df

def log_campaign_to_sheet(df, delivery_method, business_name):
    if not spreadsheet:
        return False
    try:
        worksheet = spreadsheet.worksheet("Campaigns")
        sms_success = len(df[df.get('SMS_Status', '') == '✅'])
        email_success = len(df[df.get('Email_Status', '') == '✅'])
        
        campaign_data = [
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            business_name,
            delivery_method,
            len(df),
            sms_success + email_success,
            "Completed"
        ]
        worksheet.append_row(campaign_data)
        return True
    except Exception as e:
        return False

# ================== STREAMLIT UI ==================
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Send Campaign", "Campaign History", "Settings"])

if page == "Send Campaign":
    with st.expander("📋 STEP 0: Download CSV Template", expanded=True):
        st.markdown("""
        **How to get Google Review Links:**
        1. Go to [Google Business Profile](https://business.google.com)
        2. Click "Get more reviews" 
        3. Copy the generated link
        """)

        template_df = pd.DataFrame({
            "Business Name": ["Joe's Diner", "Joe's Diner"],
            "Customer Name": ["Alice Smith", "Bob Johnson"], 
            "Email": ["alice@email.com", "bob@email.com"],
            "Phone": ["+15551234567", "+15557654321"],
            "Service Date": ["2024-01-15", "2024-01-16"],
            "Review Link": [
                "https://search.google.com/local/writereview?placeid=ChIJP3Sa8zYEwokRc5cnVJ2jWjA",
                "https://search.google.com/local/writereview?placeid=ChIJP3Sa8zYEwokRc5cnVJ2jWjA"
            ]
        })
        
        csv_buffer = io.StringIO()
        template_df.to_csv(csv_buffer, index=False)
        st.download_button(
            "📥 Download CSV Template", 
            data=csv_buffer.getvalue(), 
            file_name="reviewgarden_template.csv", 
            mime="text/csv"
        )

    st.subheader("Step 1: Upload CSV")
    uploaded_file = st.file_uploader("Customer CSV", type="csv")
    
    if uploaded_file:
        df = pd.read_csv(uploaded_file)
        df.columns = df.columns.str.strip()
        
        if check_csv(df):
            st.success(f"✅ CSV loaded: {len(df)} customers")
            st.session_state.df_processed = df
            
            with st.expander("Preview Data"):
                st.dataframe(df.head(3))

    if st.session_state.df_processed is not None:
        df = st.session_state.df_processed
        
        st.subheader("Step 2: Generate Messages")
        if st.button("Generate AI Messages", type="primary"):
            df = generate_messages_batch(df)
            st.session_state.df_processed = df
            st.success("✅ Messages generated!")
            
            with st.expander("Preview Messages"):
                for idx, row in df.head(3).iterrows():
                    st.write(f"**{row['Customer Name']}**: {row['Generated_Message']}")

        st.subheader("Step 3: Send Campaign")
        col1, col2 = st.columns(2)
        
        with col1:
            delivery = st.radio("Delivery Method", ["SMS only", "Email only", "Both"])
        
        with col2:
            email_subject = st.text_input("Email Subject", "Share your experience with us!")
            confirm_send = st.checkbox("I have permission to contact these customers")
            business_name = st.text_input(
                "Business Name", 
                value=df["Business Name"].iloc[0] if len(df["Business Name"].unique()) == 1 else ""
            )

        if st.button("🚀 Launch Campaign", type="primary") and confirm_send and business_name:
            total_sent = 0
            
            # Initialize status columns
            for col in ['SMS_Status', 'Email_Status', 'Error']:
                if col not in df.columns:
                    df[col] = ''

            st.info("Campaign starting...")
            
            if delivery in ["SMS only", "Both"]:
                st.subheader("📱 Sending SMS...")
                df, sms_sent, sms_failed = send_sms(df)
                total_sent += sms_sent
                st.success(f"SMS: {sms_sent} sent, {sms_failed} failed")
            
            if delivery in ["Email only", "Both"]:
                st.subheader("📧 Sending Emails...")
                df, email_sent, email_failed = send_email(df, email_subject)
                total_sent += email_sent
                st.success(f"Emails: {email_sent} sent, {email_failed} failed")

            # Log campaign
            if log_campaign_to_sheet(df, delivery, business_name):
                st.success("✅ Campaign logged to history")

            st.balloons()
            st.success(f"🎉 Campaign completed! Total messages sent: {total_sent}")
            
            with st.expander("Campaign Results"):
                st.dataframe(df[["Customer Name", "SMS_Status", "Email_Status", "Error"]].fillna(""))
            
            st.download_button(
                "📥 Download Results", 
                df.to_csv(index=False), 
                file_name=f"campaign_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", 
                mime="text/csv"
            )
            
            st.session_state.df_processed = None

elif page == "Campaign History":
    st.header("📊 Campaign History")
    if spreadsheet:
        try:
            worksheet = spreadsheet.worksheet("Campaigns")
            records = worksheet.get_all_records()
            if records:
                hist_df = pd.DataFrame(records)
                st.dataframe(hist_df)
            else:
                st.info("No campaigns yet. Send your first campaign to see history.")
        except:
            st.info("No campaign history yet.")
    else:
        st.info("Connect Google Sheets to see history.")

elif page == "Settings":
    st.header("⚙️ Settings")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.write("**Twilio SMS**")
        st.success("✅ Connected" if twilio_client else "❌ Not configured")
    with col2:
        st.write("**OpenAI**")
        st.success("✅ Connected" if client else "❌ Not configured")
    with col3:
        st.write("**Google Sheets**")
        st.success("✅ Connected" if spreadsheet else "❌ Not configured")
    
    st.subheader("Compliance")
    st.markdown("""
    - ✅ Must have explicit permission to contact customers
    - ✅ Include opt-out instructions in all messages  
    - ✅ Never incentivize or pay for reviews
    - ✅ Honor all opt-out requests immediately
    """)

st.markdown("---")
st.markdown("🌿 **ReviewGarden** - Grow your reputation honestly")
