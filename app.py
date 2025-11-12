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
        return Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    return None

twilio_client = init_twilio()

# ================== SESSION STATE INITIALIZATION ==================
# Initialize all session state variables at once to prevent reload issues
if "initialized" not in st.session_state:
    st.session_state.initialized = True
    st.session_state.df_processed = None
    st.session_state.messages_generated = False
    st.session_state.campaign_sent = False
    st.session_state.current_step = 1
    st.session_state.test_mode = False
    st.session_state.campaign_results = None
    st.session_state.sending_in_progress = False

# ================== UTILITY FUNCTIONS ==================
def validate_phone_number(phone):
    """Validate phone number format"""
    if pd.isna(phone):
        return False, "Missing phone number"
    
    phone_str = str(phone).strip()
    # Remove common formatting
    phone_clean = re.sub(r'[\s\-\(\)\.]', '', phone_str)
    
    # Check if it starts with + and has 10-15 digits
    if re.match(r'^\+\d{10,15}$', phone_clean):
        return True, phone_clean
    
    return False, f"Invalid format: {phone_str}"

def check_csv(df):
    """Validate CSV has required columns"""
    required = ["Business Name","Customer Name","Email","Phone","Service Date","Review Link"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"Missing required columns: {', '.join(missing)}")
        return False
    return True

def validate_csv_data(df):
    """Validate and clean CSV data"""
    issues = []
    
    for idx, row in df.iterrows():
        # Validate phone numbers
        is_valid, result = validate_phone_number(row['Phone'])
        if not is_valid:
            issues.append(f"Row {idx+2}: {result}")
        else:
            df.at[idx, 'Phone'] = result  # Store cleaned phone
    
    return df, issues

def generate_message(business_name, customer_name, service_type=""):
    """Generate personalized review request messages"""
    templates = [
        f"Hi {customer_name}! We hope you enjoyed your experience at {business_name}. Your feedback means the world to us! Would you mind sharing a quick Google review?",
        f"Hey {customer_name}! Thanks for choosing {business_name}. We'd love to hear about your experience. Could you leave us a Google review?",
        f"Hi {customer_name}! Thank you for visiting {business_name}. If you had a great experience, we'd really appreciate a Google review!",
        f"Hello {customer_name}! We loved having you at {business_name}. Would you take a moment to share your thoughts in a Google review?",
        f"Hi {customer_name}! Your opinion matters to us at {business_name}. Could you help others by leaving a quick Google review?"
    ]
    
    if service_type and service_type.strip():
        service_templates = [
            f"Hi {customer_name}! We hope you loved your {service_type} at {business_name}. Would you share your experience with a Google review?",
            f"Hey {customer_name}! Thanks for choosing {business_name} for your {service_type}. Mind leaving us a quick review?"
        ]
        templates.extend(service_templates)
    
    return random.choice(templates)

def generate_messages_batch(df):
    """Generate messages for entire dataframe"""
    df_processed = df.copy()
    messages = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for idx, row in df_processed.iterrows():
        if pd.isna(row['Customer Name']) or pd.isna(row['Business Name']):
            messages.append("")
            continue
        
        service_type = row.get('Service Type', '') if 'Service Type' in df.columns else ''
        
        message = generate_message(
            str(row["Business Name"]),
            str(row["Customer Name"]),
            str(service_type) if not pd.isna(service_type) else ""
        )
        messages.append(message)
        
        # Update progress
        progress = (idx + 1) / len(df_processed)
        progress_bar.progress(progress)
        status_text.text(f"Generating messages... {idx+1}/{len(df_processed)}")
    
    progress_bar.empty()
    status_text.empty()
    
    df_processed["Generated_Message"] = messages
    return df_processed

def send_sms_with_rate_limit(df, test_mode=False, delay_seconds=1):
    """Send SMS with rate limiting and progress tracking"""
    if not twilio_client and not test_mode:
        st.error("Twilio not configured")
        return df, 0, len(df)
    
    sent, failed, skipped = 0, 0, 0
    
    # Initialize status columns
    for col in ['SMS_Status', 'Error', 'Sent_Time']:
        if col not in df.columns:
            df[col] = ''
    
    # Create progress tracking
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, row in df.iterrows():
        try:
            # Skip invalid rows
            if pd.isna(row['Customer Name']) or pd.isna(row['Phone']):
                df.at[i, "SMS_Status"] = "⏭️ Skipped"
                df.at[i, "Error"] = "Missing name or phone"
                skipped += 1
                continue
            
            # Check for generated message
            if 'Generated_Message' not in df.columns or pd.isna(row['Generated_Message']):
                df.at[i, "SMS_Status"] = "❌ Failed"
                df.at[i, "Error"] = "No generated message"
                failed += 1
                continue
            
            message = f"{row['Generated_Message']} {row['Review Link']} Reply STOP to opt out."
            
            if test_mode:
                # Test mode - just simulate
                time.sleep(0.1)  # Simulate API call
                df.at[i, "SMS_Status"] = "🧪 Test"
                df.at[i, "Sent_Time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sent += 1
            else:
                # Real send
                twilio_client.messages.create(
                    body=message,
                    from_=TWILIO_PHONE,
                    to=str(row["Phone"]).strip()
                )
                
                df.at[i, "SMS_Status"] = "✅ Sent"
                df.at[i, "Sent_Time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                sent += 1
                
                # Rate limiting
                time.sleep(delay_seconds)
            
            # Update progress
            progress = (i + 1) / len(df)
            progress_bar.progress(progress)
            status_text.text(f"{'Testing' if test_mode else 'Sending'} messages... {i+1}/{len(df)} (Sent: {sent}, Failed: {failed}, Skipped: {skipped})")
            
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
    
    # Display current step indicator
    steps = ["📤 Upload CSV", "✍️ Generate Messages", "🚀 Send Campaign"]
    cols = st.columns(3)
    for i, (col, step) in enumerate(zip(cols, steps), 1):
        if i < st.session_state.current_step:
            col.success(f"✅ {step}")
        elif i == st.session_state.current_step:
            col.info(f"▶️ {step}")
        else:
            col.text(f"⏸️ {step}")
    
    st.markdown("---")
    
    # STEP 0: Template Download
    with st.expander("📋 STEP 0: Download CSV Template", expanded=False):
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
        st.info("💡 **Tip**: Service Type is optional but helps personalize messages!")

    # STEP 1: Upload CSV
    st.subheader("📤 Step 1: Upload Customer CSV")
    uploaded_file = st.file_uploader("Choose your customer CSV file", type="csv", key="uploader")
    
    if uploaded_file:
        df = pd.read_csv(uploaded_file)
        df.columns = df.columns.str.strip()
        
        if check_csv(df):
            # Validate and clean data
            df_clean, issues = validate_csv_data(df)
            
            if issues:
                st.warning(f"⚠️ Found {len(issues)} validation issues:")
                with st.expander("View Issues"):
                    for issue in issues[:10]:  # Show first 10
                        st.text(issue)
                    if len(issues) > 10:
                        st.text(f"... and {len(issues)-10} more issues")
            
            st.success(f"✅ CSV loaded: {len(df_clean)} customers")
            st.session_state.df_processed = df_clean
            st.session_state.current_step = 2
            
            with st.expander("📊 Preview Data"):
                st.dataframe(df_clean)

    # STEP 2: Generate Messages
    if st.session_state.current_step >= 2 and st.session_state.df_processed is not None:
        st.subheader("✍️ Step 2: Generate Messages")
        
        if 'Generated_Message' not in st.session_state.df_processed.columns:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.info("Click below to generate personalized messages for each customer")
            with col2:
                if st.button("🎯 Generate Messages", type="primary", use_container_width=True):
                    with st.spinner("Generating personalized messages..."):
                        df_processed = generate_messages_batch(st.session_state.df_processed)
                        st.session_state.df_processed = df_processed
                        st.session_state.messages_generated = True
                        st.session_state.current_step = 3
                        st.success("✅ Messages generated successfully!")
                        st.rerun()
        else:
            st.success("✅ Messages already generated!")
            
            with st.expander("👀 Preview Generated Messages", expanded=True):
                preview_count = st.slider("Number of messages to preview", 1, min(10, len(st.session_state.df_processed)), 5)
                for idx, row in st.session_state.df_processed.head(preview_count).iterrows():
                    if not pd.isna(row['Customer Name']):
                        st.markdown(f"**{row['Customer Name']}** ({row['Phone']})")
                        st.text(f"{row.get('Generated_Message', 'No message')}")
                        st.markdown("---")
            
            if st.button("🔄 Regenerate All Messages"):
                df_processed = generate_messages_batch(st.session_state.df_processed)
                st.session_state.df_processed = df_processed
                st.success("✅ Messages regenerated!")
                st.rerun()

    # STEP 3: Send Campaign
    if (st.session_state.current_step >= 3 and 
        st.session_state.df_processed is not None and 
        'Generated_Message' in st.session_state.df_processed.columns and
        not st.session_state.campaign_sent):
        
        st.subheader("🚀 Step 3: Launch Campaign")
        
        # Campaign settings
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.session_state.test_mode = st.checkbox(
                "🧪 Test Mode (Don't actually send)", 
                value=st.session_state.test_mode,
                help="Preview what will happen without sending real SMS"
            )
        
        with col2:
            confirm_send = st.checkbox(
                "✓ I have permission to contact these customers",
                help="Required: Confirm you have consent to send SMS"
            )
        
        with col3:
            rate_limit = st.number_input(
                "Delay between messages (seconds)",
                min_value=0.5,
                max_value=5.0,
                value=1.0,
                step=0.5,
                help="Prevents rate limiting issues"
            )
        
        # Summary stats
        st.markdown("### 📊 Campaign Summary")
        summary_cols = st.columns(4)
        total_customers = len(st.session_state.df_processed)
        valid_phones = st.session_state.df_processed['Phone'].notna().sum()
        
        summary_cols[0].metric("Total Customers", total_customers)
        summary_cols[1].metric("Valid Phone Numbers", valid_phones)
        summary_cols[2].metric("Messages Ready", len(st.session_state.df_processed[st.session_state.df_processed['Generated_Message'].notna()]))
        summary_cols[3].metric("Estimated Time", f"{int(valid_phones * rate_limit / 60)} min")
        
        st.markdown("---")
        
        # Launch button
        if confirm_send or st.session_state.test_mode:
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                launch_button = st.button(
                    f"{'🧪 Run Test Campaign' if st.session_state.test_mode else '🚀 LAUNCH CAMPAIGN'}",
                    type="primary",
                    use_container_width=True,
                    disabled=st.session_state.sending_in_progress
                )
                
                if launch_button:
                    st.session_state.sending_in_progress = True
                    
                    df = st.session_state.df_processed.copy()
                    
                    st.info(f"{'🧪 Test campaign' if st.session_state.test_mode else '📤 Campaign'} starting...")
                    
                    # Send SMS
                    df, sms_sent, sms_failed, sms_skipped = send_sms_with_rate_limit(
                        df, 
                        test_mode=st.session_state.test_mode,
                        delay_seconds=rate_limit
                    )
                    
                    # Update session state
                    st.session_state.df_processed = df
                    st.session_state.campaign_sent = True
                    st.session_state.campaign_results = {
                        'sent': sms_sent,
                        'failed': sms_failed,
                        'skipped': sms_skipped,
                        'timestamp': datetime.now(),
                        'test_mode': st.session_state.test_mode
                    }
                    st.session_state.sending_in_progress = False
                    st.session_state.current_step = 4
                    
                    if not st.session_state.test_mode:
                        st.balloons()
                    
                    st.rerun()
        else:
            st.warning("⚠️ Please confirm you have permission to contact these customers")
    
    # STEP 4: Results (after campaign sent)
    if st.session_state.campaign_sent and st.session_state.campaign_results:
        st.markdown("---")
        st.subheader("✅ Campaign Complete!")
        
        results = st.session_state.campaign_results
        
        if results['test_mode']:
            st.info("🧪 This was a TEST campaign - no actual messages were sent")
        
        # Results metrics
        result_cols = st.columns(4)
        result_cols[0].metric("✅ Sent", results['sent'], delta=None)
        result_cols[1].metric("❌ Failed", results['failed'], delta=None)
        result_cols[2].metric("⏭️ Skipped", results['skipped'], delta=None)
        result_cols[3].metric("📊 Success Rate", f"{(results['sent']/(results['sent']+results['failed'])*100 if results['sent']+results['failed'] > 0 else 0):.1f}%")
        
        # Detailed results
        with st.expander("📋 Detailed Campaign Results", expanded=True):
            display_df = st.session_state.df_processed[["Customer Name", "Phone", "SMS_Status", "Sent_Time", "Error"]].fillna("")
            st.dataframe(display_df, use_container_width=True)
        
        # Export results
        col1, col2, col3 = st.columns(3)
        
        with col1:
            csv_results = st.session_state.df_processed.to_csv(index=False)
            st.download_button(
                "📥 Download Results CSV",
                data=csv_results,
                file_name=f"campaign_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                use_container_width=True
            )
        
        with col2:
            if st.button("🔄 Start New Campaign", use_container_width=True):
                # Reset all session state
                st.session_state.df_processed = None
                st.session_state.messages_generated = False
                st.session_state.campaign_sent = False
                st.session_state.current_step = 1
                st.session_state.campaign_results = None
                st.session_state.sending_in_progress = False
                st.rerun()

elif page == "Campaign History":
    st.header("📊 Campaign History")
    
    if st.session_state.campaign_results:
        st.subheader("Latest Campaign")
        results = st.session_state.campaign_results
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Campaign Date", results['timestamp'].strftime("%Y-%m-%d %H:%M:%S"))
            st.metric("Mode", "Test" if results['test_mode'] else "Live")
        with col2:
            st.metric("Total Sent", results['sent'])
            st.metric("Failed", results['failed'])
    else:
        st.info("No campaign history yet. Send your first campaign to see results here!")

elif page == "Settings":
    st.header("⚙️ Settings")
    
    st.subheader("📱 Twilio SMS Configuration")
    if twilio_client:
        st.success("✅ Twilio is connected and ready")
        st.code(f"Phone Number: {TWILIO_PHONE}")
    else:
        st.error("❌ Twilio not configured")
        st.markdown("""
        **To configure Twilio:**
        1. Create a `.env` file in your project directory
        2. Add the following variables:
        ```
        TWILIO_ACCOUNT_SID=your_account_sid
        TWILIO_AUTH_TOKEN=your_auth_token
        TWILIO_PHONE_NUMBER=your_twilio_phone
        ```
        3. Restart the application
        """)
    
    st.markdown("---")
    st.subheader("🔒 Data Privacy")
    st.info("Customer data is only stored in memory during your session. No data is saved to disk.")
    
    if st.button("🗑️ Clear All Session Data"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.success("All session data cleared!")
        st.rerun()

# Footer
st.markdown("---")
st.markdown("🌿 **ReviewGarden** - Grow your reputation honestly | Made with Streamlit")
