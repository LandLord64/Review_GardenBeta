import streamlit as st
import pandas as pd
import os
import io
import random
import re
import time
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
        try:
            return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        except:
            return None
    return None

twilio_client = init_twilio()

# ================== SESSION STATE ==================
if "df_processed" not in st.session_state:
    st.session_state.df_processed = None
if "messages_generated" not in st.session_state:
    st.session_state.messages_generated = False
if "campaign_sent" not in st.session_state:
    st.session_state.campaign_sent = False
if "current_step" not in st.session_state:
    st.session_state.current_step = 1
if "test_mode" not in st.session_state:
    st.session_state.test_mode = False
if "campaign_results" not in st.session_state:
    st.session_state.campaign_results = None

# ================== UTILITY FUNCTIONS ==================
def validate_phone_number(phone):
    if pd.isna(phone) or phone == '':
        return False, "Missing phone number"
    
    phone_str = str(phone).strip()
    
    if 'E' in phone_str.upper() or 'e' in phone_str:
        try:
            phone_str = "{:.0f}".format(float(phone_str))
        except:
            return False, "Invalid format"
    
    if phone_str.endswith('.0'):
        phone_str = phone_str[:-2]
    
    phone_clean = re.sub(r'[\s\-\(\)]', '', phone_str)
    
    if not phone_clean.startswith('+'):
        digits_only = re.sub(r'\D', '', phone_clean)
        if len(digits_only) == 10:
            phone_clean = "+1" + digits_only
        elif len(digits_only) == 11 and digits_only.startswith('1'):
            phone_clean = "+" + digits_only
        else:
            return False, "Invalid format: need 10-11 digits"
    else:
        digits = re.sub(r'\D', '', phone_clean)
        phone_clean = "+" + digits
    
    regex_pattern = r'^\+\d{10,15}$'
    if re.match(regex_pattern, phone_clean):
        return True, phone_clean
    
    return False, "Invalid phone format"

def parse_service_date(date_value):
    if pd.isna(date_value) or date_value == '':
        return None, None
    
    try:
        if isinstance(date_value, (int, float)):
            parsed_date = pd.to_datetime(date_value, unit='D', origin='1899-12-30')
        else:
            parsed_date = pd.to_datetime(date_value)
        
        formatted_date = parsed_date.strftime("%B %d, %Y")
        return formatted_date, None
    except:
        return str(date_value), "Could not parse date"

def check_csv(df):
    required = ["Business Name","Customer Name","Email","Phone","Service Date","Review Link"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error("Missing required columns: {}".format(', '.join(missing)))
        return False
    return True

def validate_csv_data(df):
    issues = []
    
    for idx, row in df.iterrows():
        is_valid, result = validate_phone_number(row['Phone'])
        if not is_valid:
            issues.append("Row {}: {}".format(idx+2, result))
        else:
            df.at[idx, 'Phone'] = result
        
        if 'Service Date' in df.columns:
            formatted_date, error = parse_service_date(row['Service Date'])
            if formatted_date:
                df.at[idx, 'Service Date'] = formatted_date
            if error:
                issues.append("Row {}: {}".format(idx+2, error))
    
    return df, issues

def generate_message(business_name, customer_name, service_type="", service_date=""):
    templates = [
        "Hi {}! We hope you enjoyed your experience at {}. Your feedback means the world to us! Would you mind sharing a quick Google review?",
        "Hey {}! Thanks for choosing {}. We'd love to hear about your experience. Could you leave us a Google review?",
        "Hi {}! Thank you for visiting {}. If you had a great experience, we'd really appreciate a Google review!",
        "Hello {}! We loved having you at {}. Would you take a moment to share your thoughts in a Google review?",
        "Hi {}! Your opinion matters to us at {}. Could you help others by leaving a quick Google review?"
    ]
    
    if service_date and service_date.strip():
        templates.extend([
            "Hi {}! Hope you enjoyed your visit to {} on {}. Would you mind leaving us a Google review?",
            "Hey {}! Thanks for visiting {} on {}. We'd love to hear about your experience in a Google review!"
        ])
    
    if service_type and service_type.strip():
        templates.extend([
            "Hi {}! We hope you loved your {} at {}. Would you share your experience with a Google review?",
            "Hey {}! Thanks for choosing {} for your {}. Mind leaving us a quick review?"
        ])
        
        if service_date and service_date.strip():
            templates.append("Hi {}! Hope you enjoyed your {} at {} on {}. Could you leave us a Google review?")
    
    template = random.choice(templates)
    
    if service_date and service_type:
        return template.format(customer_name, service_type, business_name, service_date)
    elif service_date:
        return template.format(customer_name, business_name, service_date)
    elif service_type:
        return template.format(customer_name, service_type, business_name)
    else:
        return template.format(customer_name, business_name)

def generate_messages_batch(df):
    df_processed = df.copy()
    messages = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, row in df_processed.iterrows():
        if pd.isna(row['Customer Name']) or pd.isna(row['Business Name']):
            messages.append("")
            continue
        
        service_type = row.get('Service Type', '') if 'Service Type' in df.columns else ''
        service_date = row.get('Service Date', '') if 'Service Date' in df.columns else ''
        
        message = generate_message(
            str(row["Business Name"]),
            str(row["Customer Name"]),
            str(service_type) if not pd.isna(service_type) and service_type else "",
            str(service_date) if not pd.isna(service_date) and service_date else ""
        )
        messages.append(message)
        
        progress = (idx + 1) / len(df_processed)
        progress_bar.progress(progress)
        status_text.text("Generating messages... {}/{}".format(idx+1, len(df_processed)))
    
    progress_bar.empty()
    status_text.empty()
    
    df_processed["Generated_Message"] = messages
    return df_processed

def send_sms_with_rate_limit(df, test_mode=False, delay_seconds=1):
    if not twilio_client and not test_mode:
        st.error("Twilio not configured")
        return df, 0, len(df), 0
    
    sent, failed, skipped = 0, 0, 0
    
    for col in ['SMS_Status', 'Error', 'Sent_Time']:
        if col not in df.columns:
            df[col] = ''
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        try:
            if pd.isna(row['Customer Name']) or pd.isna(row['Phone']):
                df.at[i, "SMS_Status"] = "⏭️ Skipped"
                df.at[i, "Error"] = "Missing name or phone"
                skipped += 1
                continue
            
            if 'Generated_Message' not in df.columns or pd.isna(row['Generated_Message']):
                df.at[i, "SMS_Status"] = "❌ Failed"
                df.at[i, "Error"] = "No generated message"
                failed += 1
                continue
            
            message = "{} {} Reply STOP to opt out.".format(row['Generated_Message'], row['Review Link'])
            
            if test_mode:
                time.sleep(0.1)
                df.at[i, "SMS_Status"] = "🧪 Test"
                df.at[i, "Sent_Time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sent += 1
            else:
                twilio_client.messages.create(
                    body=message,
                    from_=TWILIO_PHONE,
                    to=str(row["Phone"]).strip()
                )
                
                df.at[i, "SMS_Status"] = "✅ Sent"
                df.at[i, "Sent_Time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sent += 1
                time.sleep(delay_seconds)
            
            progress = (i + 1) / len(df)
            progress_bar.progress(progress)
            status_text.text("{} messages... {}/{} (Sent: {}, Failed: {}, Skipped: {})".format(
                'Testing' if test_mode else 'Sending', i+1, len(df), sent, failed, skipped))
            
        except Exception as e:
            df.at[i, "SMS_Status"] = "❌ Failed"
            df.at[i, "Error"] = str(e)
            failed += 1
    
    progress_bar.empty()
    status_text.empty()
    
    return df, sent, failed, skipped

# ================== STREAMLIT UI ==================
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Send Campaign", "Campaign History", "Settings"])

if page == "Send Campaign":
    
    steps = ["📤 Upload CSV", "✍️ Generate Messages", "🚀 Send Campaign"]
    cols = st.columns(3)
    for i, (col, step) in enumerate(zip(cols, steps), 1):
        if i < st.session_state.current_step:
            col.success("✅ {}".format(step))
        elif i == st.session_state.current_step:
            col.info("▶️ {}".format(step))
        else:
            col.text("⏸️ {}".format(step))
    
    st.markdown("---")
    
    with st.expander("📋 Download CSV Template", expanded=False):
        template_df = pd.DataFrame({
            "Business Name": ["Garden Cafe"],
            "Customer Name": ["John Smith"], 
            "Email": ["john@example.com"],
            "Phone": ["+15555550100"],
            "Service Date": ["2024-01-15"],
            "Service Type": ["Lunch Service"],
            "Review Link": ["https://search.google.com/local/writereview?placeid=YOUR_PLACE_ID"]
        })
        
        csv_buffer = io.StringIO()
        template_df.to_csv(csv_buffer, index=False)
        st.download_button(
            "📥 Download CSV Template", 
            data=csv_buffer.getvalue(), 
            file_name="reviewgarden_template.csv", 
            mime="text/csv"
        )

    st.subheader("📤 Step 1: Upload Customer CSV")
    uploaded_file = st.file_uploader("Choose your customer CSV file", type="csv")
    
    if uploaded_file:
        df = pd.read_csv(uploaded_file)
        df.columns = df.columns.str.strip()
        
        if check_csv(df):
            df_clean, issues = validate_csv_data(df)
            
            if issues:
                st.warning("⚠️ Found {} validation issues".format(len(issues)))
                with st.expander("View Issues"):
                    for issue in issues[:10]:
                        st.text(issue)
                    if len(issues) > 10:
                        st.text("... and {} more issues".format(len(issues)-10))
            
            st.success("✅ CSV loaded: {} customers".format(len(df_clean)))
            st.session_state.df_processed = df_clean
            if st.session_state.current_step == 1:
                st.session_state.current_step = 2
            
            with st.expander("📊 Preview Data"):
                st.dataframe(df_clean)

    # STEP 2
    if st.session_state.current_step >= 2 and st.session_state.df_processed is not None:
        st.markdown("---")
        st.subheader("✍️ Step 2: Generate Messages")
        
        has_messages = 'Generated_Message' in st.session_state.df_processed.columns
        
        if not has_messages:
            st.info("Click below to generate personalized messages for each customer")
            
            if st.button("🎯 Generate Messages", type="primary", key="gen_btn"):
                with st.spinner("Generating..."):
                    df_processed = generate_messages_batch(st.session_state.df_processed)
                    st.session_state.df_processed = df_processed
                    st.session_state.messages_generated = True
                    st.session_state.current_step = 3
                    st.success("✅ Messages generated!")
                    time.sleep(1)
                    st.rerun()
        else:
            st.success("✅ Messages generated!")
            
            with st.expander("👀 Preview Messages"):
                for idx, row in st.session_state.df_processed.head(5).iterrows():
                    if not pd.isna(row['Customer Name']):
                        st.markdown("**{}** ({})".format(row['Customer Name'], row['Phone']))
                        st.text(row.get('Generated_Message', ''))
                        st.markdown("---")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("➡️ Continue to Step 3", type="primary", key="continue_btn"):
                    st.session_state.current_step = 3
                    st.rerun()
            with col2:
                if st.button("🔄 Regenerate", key="regen_btn"):
                    st.session_state.df_processed = st.session_state.df_processed.drop(columns=['Generated_Message'])
                    st.session_state.messages_generated = False
                    st.rerun()

    # STEP 3
    if (st.session_state.current_step >= 3 and 
        st.session_state.df_processed is not None and 
        'Generated_Message' in st.session_state.df_processed.columns and
        not st.session_state.campaign_sent):
        
        st.markdown("---")
        st.subheader("🚀 Step 3: Launch Campaign")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            test_mode = st.checkbox("🧪 Test Mode", value=st.session_state.test_mode)
            st.session_state.test_mode = test_mode
        
        with col2:
            confirm = st.checkbox("✓ I have permission")
        
        with col3:
            delay = st.number_input("Delay (sec)", 0.5, 5.0, 1.0, 0.5)
        
        st.markdown("### 📊 Summary")
        cols = st.columns(4)
        total = len(st.session_state.df_processed)
        valid = st.session_state.df_processed['Phone'].notna().sum()
        
        cols[0].metric("Total", total)
        cols[1].metric("Valid Phones", valid)
        cols[2].metric("Ready", len(st.session_state.df_processed[st.session_state.df_processed['Generated_Message'].notna()]))
        cols[3].metric("Est. Time", "{} min".format(int(valid * delay / 60)))
        
        st.markdown("---")
        
        if confirm or test_mode:
            if st.button("{}".format('🧪 Test' if test_mode else '🚀 LAUNCH'), type="primary", use_container_width=True):
                df = st.session_state.df_processed.copy()
                
                st.info("{} starting...".format('Test' if test_mode else 'Campaign'))
                
                df, sent, failed, skipped = send_sms_with_rate_limit(df, test_mode, delay)
                
                st.session_state.df_processed = df
                st.session_state.campaign_sent = True
                st.session_state.campaign_results = {
                    'sent': sent,
                    'failed': failed,
                    'skipped': skipped,
                    'timestamp': datetime.now(),
                    'test_mode': test_mode
                }
                
                if not test_mode:
                    st.balloons()
                
                st.rerun()
        else:
            st.warning("⚠️ Please confirm permission")
    
    # RESULTS
    if st.session_state.campaign_sent and st.session_state.campaign_results:
        st.markdown("---")
        st.subheader("✅ Complete!")
        
        r = st.session_state.campaign_results
        
        if r['test_mode']:
            st.info("🧪 Test mode - no real messages sent")
        
        cols = st.columns(4)
        cols[0].metric("✅ Sent", r['sent'])
        cols[1].metric("❌ Failed", r['failed'])
        cols[2].metric("⏭️ Skipped", r['skipped'])
        rate = (r['sent']/(r['sent']+r['failed'])*100 if r['sent']+r['failed'] > 0 else 0)
        cols[3].metric("Success", "{:.1f}%".format(rate))
        
        with st.expander("📋 Results"):
            st.dataframe(st.session_state.df_processed[["Customer Name", "Phone", "SMS_Status", "Sent_Time", "Error"]].fillna(""))
        
        col1, col2 = st.columns(2)
        with col1:
            csv = st.session_state.df_processed.to_csv(index=False)
            st.download_button("📥 Download", csv, "results_{}.csv".format(datetime.now().strftime('%Y%m%d_%H%M%S')), "text/csv")
        with col2:
            if st.button("🔄 New Campaign"):
                for key in ['df_processed', 'messages_generated', 'campaign_sent', 'campaign_results']:
                    st.session_state[key] = None if 'results' in key else False
                st.session_state.current_step = 1
                st.rerun()

elif page == "Campaign History":
    st.header("📊 Campaign History")
    
    if st.session_state.campaign_results:
        st.subheader("Latest Campaign")
        r = st.session_state.campaign_results
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Date", r['timestamp'].strftime("%Y-%m-%d %H:%M"))
            st.metric("Mode", "Test" if r['test_mode'] else "Live")
        with col2:
            st.metric("Sent", r['sent'])
            st.metric("Failed", r['failed'])
    else:
        st.info("No history yet")

elif page == "Settings":
    st.header("⚙️ Settings")
    
    st.subheader("📱 Twilio")
    if twilio_client:
        st.success("✅ Connected")
        st.code("Phone: {}".format(TWILIO_PHONE))
    else:
        st.error("❌ Not configured")
        st.markdown("""
        Create `.env` file:
        ```
        TWILIO_ACCOUNT_SID=your_sid
        TWILIO_AUTH_TOKEN=your_token
        TWILIO_PHONE_NUMBER=your_phone
        ```
        """)
    
    st.markdown("---")
    if st.button("🗑️ Clear Session"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

st.markdown("---")
st.markdown("🌿 **ReviewGarden** - Grow your reputation honestly")
