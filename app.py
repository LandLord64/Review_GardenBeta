import streamlit as st
import pandas as pd
import os
import io
import random
from datetime import datetime
from twilio.rest import Client
from dotenv import load_dotenv

# ================== LOAD ENV ==================
load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.getenv("TWILIO_PHONE_NUMBER")

st.set_page_config(page_title="ReviewGarden", page_icon="🌿", layout="wide")
st.title("🌿 ReviewGarden - Review Booster")

# ================== INIT SERVICES ==================
@st.cache_resource
def init_twilio():
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return None

twilio_client = init_twilio()

# ================== SESSION STATE ==================
if "df_processed" not in st.session_state:
    st.session_state.df_processed = None
if "messages_generated" not in st.session_state:
    st.session_state.messages_generated = False

# ================== UTILITY FUNCTIONS ==================
def check_csv(df):
    required = ["Business Name","Customer Name","Email","Phone","Service Date","Review Link"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Missing required columns: {', '.join(missing)}")
        return False
    return True

def generate_message(business_name, customer_name, service_type=""):
    """Simple fallback messages without OpenAI"""
    messages = [
        f"Hi {customer_name}! Hope you loved your experience at {business_name}. Would you mind leaving a Google review?",
        f"Hey {customer_name}! Thanks for choosing {business_name}. Could you share your experience with a quick Google review?",
        f"Hi {customer_name}! We hope you enjoyed your time at {business_name}. Please consider leaving us a Google review!"
    ]
    return random.choice(messages)

def generate_messages_batch(df):
    """Generate messages for entire dataframe"""
    # Create a copy to avoid modifying the original
    df_processed = df.copy()
    messages = []
    
    for idx, row in df_processed.iterrows():
        # Skip rows with missing data
        if pd.isna(row['Customer Name']) or pd.isna(row['Business Name']):
            messages.append("")
            continue
            
        message = generate_message(
            str(row["Business Name"]),
            str(row["Customer Name"])
        )
        messages.append(message)
        st.write(f"✅ Generated message for {row['Customer Name']}: {message}")
    
    # CRITICAL FIX: Assign to the DataFrame copy
    df_processed["Generated_Message"] = messages
    st.session_state.messages_generated = True
    return df_processed

def send_sms(df):
    if not twilio_client:
        st.error("Twilio not configured")
        return df, 0, len(df)
        
    sent, failed = 0, 0
    
    for i, row in df.iterrows():
        try:
            # Skip rows with missing data
            if pd.isna(row['Customer Name']) or pd.isna(row['Phone']):
                continue
                
            # Check if message exists
            if 'Generated_Message' not in df.columns or pd.isna(row['Generated_Message']):
                st.error(f"❌ No generated message for {row['Customer Name']}")
                failed += 1
                continue
            
            message = f"{row['Generated_Message']} {row['Review Link']} Reply STOP to opt out."
            
            st.write(f"📱 Sending to: {row['Phone']}")
            st.write(f"Message: {message}")
            
            # Send SMS
            twilio_client.messages.create(
                body=message,
                from_=TWILIO_PHONE,
                to=str(row["Phone"]).strip()
            )
            
            if 'SMS_Status' not in df.columns:
                df['SMS_Status'] = ''
            df.at[i, "SMS_Status"] = "✅"
            sent += 1
            st.success(f"✅ SMS sent to: {row['Phone']}")
            
        except Exception as e:
            if 'SMS_Status' not in df.columns:
                df['SMS_Status'] = ''
            if 'Error' not in df.columns:
                df['Error'] = ''
            df.at[i, "SMS_Status"] = "❌"
            df.at[i, "Error"] = str(e)
            failed += 1
            st.error(f"❌ SMS failed: {str(e)}")
    
    return df, sent, failed

# ================== STREAMLIT UI ==================
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Send Campaign", "Settings"])

if page == "Send Campaign":
    with st.expander("📋 STEP 0: Download CSV Template", expanded=True):
        template_df = pd.DataFrame({
            "Business Name": ["Test Business"],
            "Customer Name": ["Test Customer"], 
            "Email": ["test@email.com"],
            "Phone": ["+17803997364"],
            "Service Date": ["2024-01-15"],
            "Review Link": ["https://search.google.com/local/writereview?placeid=TEST"]
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
                st.dataframe(df)

    if st.session_state.df_processed is not None:
        df = st.session_state.df_processed
        
        st.subheader("Step 2: Generate Messages")
        if st.button("Generate Messages", type="primary"):
            with st.spinner("Generating messages..."):
                df_processed = generate_messages_batch(df)
                st.session_state.df_processed = df_processed
                st.success("✅ Messages generated!")
                
                with st.expander("Preview Messages"):
                    for idx, row in df_processed.iterrows():
                        if not pd.isna(row['Customer Name']):
                            st.write(f"**{row['Customer Name']}**: {row.get('Generated_Message', 'No message')}")

        # Check if messages were generated
        if (st.session_state.df_processed is not None and 
            'Generated_Message' in st.session_state.df_processed.columns):
            
            st.subheader("Step 3: Send Campaign")
            confirm_send = st.checkbox("I have permission to contact these customers")
            business_name = st.text_input("Business Name", value="Test Business")

            if st.button("🚀 Launch Campaign", type="primary") and confirm_send and business_name:
                df = st.session_state.df_processed
                
                # Initialize status columns
                for col in ['SMS_Status', 'Error']:
                    if col not in df.columns:
                        df[col] = ''

                st.info("Campaign starting...")
                
                st.subheader("📱 Sending SMS...")
                df, sms_sent, sms_failed = send_sms(df)
                
                # Update session state
                st.session_state.df_processed = df
                
                st.balloons()
                st.success(f"🎉 Campaign completed! SMS sent: {sms_sent}, Failed: {sms_failed}")
                
                with st.expander("Campaign Results"):
                    st.dataframe(df[["Customer Name", "Phone", "SMS_Status", "Error"]].fillna(""))
        else:
            st.info("👆 Generate messages first to enable campaign sending")

elif page == "Settings":
    st.header("⚙️ Settings")
    st.write("**Twilio SMS**")
    st.success("✅ Connected" if twilio_client else "❌ Not configured")

st.markdown("---")
st.markdown("🌿 **ReviewGarden** - Grow your reputation honestly")

