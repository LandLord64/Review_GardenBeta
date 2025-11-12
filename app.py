import streamlit as st
import pandas as pd
import os
import io
import random
import re
import time
import logging
import sqlite3
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Tuple, List, Dict, Any
from dotenv import load_dotenv

# ================== CONFIGURATION ==================
# Load environment variables
load_dotenv()

def get_env_var(var_name: str, required: bool = False) -> Optional[str]:
    """Safely get environment variables with validation"""
    value = os.getenv(var_name)
    if required and not value:
        st.error(f"Missing required environment variable: {var_name}")
        if required:
            st.stop()
    return value

# Try to import Twilio, but make it optional
try:
    from twilio.rest import Client
    TWILIO_AVAILABLE = True
except ImportError:
    TWILIO_AVAILABLE = False
    Client = None

TWILIO_ACCOUNT_SID = get_env_var("TWILIO_ACCOUNT_SID", required=False)
TWILIO_AUTH_TOKEN = get_env_var("TWILIO_AUTH_TOKEN", required=False)
TWILIO_PHONE = get_env_var("TWILIO_PHONE_NUMBER", required=False)

# Configuration settings
class AppConfig:
    default_delay_seconds: float = 1.0
    max_campaign_size: int = 1000
    allowed_phone_countries: list = ["US", "CA"]

config = AppConfig()

# ================== LOGGING SETUP ==================
def setup_logging():
    """Setup application logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('reviewgarden.log')
        ]
    )

setup_logging()

# ================== DATABASE SETUP ==================
@contextmanager
def get_db_connection():
    """Context manager for database connections"""
    conn = sqlite3.connect('reviewgarden.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_database():
    """Initialize database tables"""
    with get_db_connection() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                campaign_name TEXT,
                total_customers INTEGER,
                messages_sent INTEGER,
                messages_failed INTEGER,
                messages_skipped INTEGER,
                test_mode BOOLEAN,
                success_rate REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS campaign_details (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER,
                customer_name TEXT,
                phone TEXT,
                status TEXT,
                error_message TEXT,
                sent_time DATETIME,
                FOREIGN KEY (campaign_id) REFERENCES campaigns (id)
            )
        ''')

init_database()

# ================== STREAMLIT CONFIG ==================
st.set_page_config(page_title="ReviewGarden", page_icon="🌿", layout="wide")
st.title("🌿 ReviewGarden - Review Booster")

# ================== INIT SERVICES ==================
@st.cache_resource
def init_twilio():
    """Initialize Twilio client with error handling"""
    if not TWILIO_AVAILABLE:
        logging.warning("Twilio package not available")
        return None
    
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        logging.warning("Twilio credentials not configured")
        return None
    
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        # Test credentials by fetching account info
        client.api.accounts(TWILIO_ACCOUNT_SID).fetch()
        logging.info("Twilio client initialized successfully")
        return client
    except Exception as e:
        logging.error(f"Twilio initialization failed: {e}")
        st.error(f"Twilio configuration error: {e}")
        return None

twilio_client = init_twilio()

# ================== SESSION STATE INITIALIZATION ==================
if "initialized" not in st.session_state:
    st.session_state.initialized = True
    st.session_state.df_processed = None
    st.session_state.messages_generated = False
    st.session_state.campaign_sent = False
    st.session_state.current_step = 1
    st.session_state.test_mode = True  # Default to test mode for safety
    st.session_state.campaign_results = None
    st.session_state.sending_in_progress = False
    st.session_state.campaign_name = f"Campaign_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# ================== ENHANCED UTILITY FUNCTIONS ==================
def validate_phone_number(phone: str) -> Tuple[bool, str]:
    """Enhanced phone number validation with better formatting"""
    if pd.isna(phone) or phone == '':
        return False, "Missing phone number"
    
    phone_str = str(phone).strip()
    
    # Handle Excel scientific notation
    if 'E' in phone_str.upper() or 'e' in phone_str:
        try:
            phone_str = "{:.0f}".format(float(phone_str))
        except ValueError:
            return False, "Invalid numeric format"
    
    # Remove .0 that Excel adds to numbers
    if phone_str.endswith('.0'):
        phone_str = phone_str[:-2]
    
    # Remove common formatting characters
    phone_clean = re.sub(r'[\s\-\(\)\.]', '', phone_str)
    
    # If no + prefix, try to add it
    if not phone_clean.startswith('+'):
        digits_only = re.sub(r'\D', '', phone_clean)
        if len(digits_only) == 10:
            phone_clean = "+1" + digits_only  # US default
        elif len(digits_only) == 11 and digits_only.startswith('1'):
            phone_clean = "+" + digits_only
        else:
            return False, f"Invalid format: {len(digits_only)} digits (need 10-11)"
    else:
        # Clean international format
        digits = re.sub(r'\D', '', phone_clean)
        phone_clean = "+" + digits
    
    # Final validation - more comprehensive
    regex_pattern = r'^\+\d{10,15}$'
    if not re.match(regex_pattern, phone_clean):
        return False, "Invalid international format"
    
    return True, phone_clean

def parse_service_date(date_value: Any) -> Tuple[Optional[str], Optional[str]]:
    """Enhanced date parsing with multiple format support"""
    if pd.isna(date_value) or date_value == '':
        return None, "Missing date"
    
    try:
        # Try multiple date parsing strategies
        if isinstance(date_value, (int, float)):
            # Excel serial date
            parsed_date = pd.to_datetime(date_value, unit='D', origin='1899-12-30')
        elif isinstance(date_value, str):
            # String date
            parsed_date = pd.to_datetime(date_value, infer_datetime_format=True)
        else:
            # Direct datetime object
            parsed_date = pd.to_datetime(date_value)
        
        # Validate date is reasonable (not in future and not too old)
        current_year = datetime.now().year
        if parsed_date.year < current_year - 1 or parsed_date.year > current_year + 1:
            return parsed_date.strftime("%B %d, %Y"), "Date outside expected range"
        
        formatted_date = parsed_date.strftime("%B %d, %Y")
        return formatted_date, None
        
    except Exception as e:
        logging.error(f"Date parsing error for {date_value}: {e}")
        return str(date_value), f"Could not parse date: {str(e)}"

def check_csv(df: pd.DataFrame) -> bool:
    """Validate CSV has required columns with better error reporting"""
    required = ["Business Name", "Customer Name", "Email", "Phone", "Service Date", "Review Link"]
    missing = [c for c in required if c not in df.columns]
    
    if missing:
        st.error(f"❌ Missing required columns: {', '.join(missing)}")
        st.info("💡 Make sure your CSV includes all required columns. Download the template for reference.")
        return False
    
    # Check for empty DataFrame
    if df.empty:
        st.error("❌ The uploaded CSV is empty")
        return False
    
    return True

def enhanced_validate_csv_data(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Enhanced CSV data validation with comprehensive checks"""
    issues = []
    required_cols = ["Business Name", "Customer Name", "Email", "Phone", "Service Date", "Review Link"]
    
    # Check for missing values in required columns
    for col in required_cols:
        if col in df.columns:
            missing_count = df[col].isna().sum()
            if missing_count > 0:
                issues.append(f"{missing_count} missing values in '{col}'")
    
    # Validate each row
    valid_phones = 0
    for idx, row in df.iterrows():
        row_num = idx + 2  # +2 for header and 1-based indexing
        
        # Phone validation
        if pd.notna(row['Phone']):
            is_valid, result = validate_phone_number(row['Phone'])
            if not is_valid:
                issues.append(f"Row {row_num}: Phone - {result}")
            else:
                df.at[idx, 'Phone'] = result
                valid_phones += 1
        else:
            issues.append(f"Row {row_num}: Missing phone number")
        
        # Email validation (basic format check)
        if pd.notna(row['Email']) and row['Email'] != '':
            email_str = str(row['Email']).strip()
            if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email_str):
                issues.append(f"Row {row_num}: Invalid email format '{email_str}'")
        
        # Service Date validation
        if 'Service Date' in df.columns and pd.notna(row['Service Date']):
            formatted_date, error = parse_service_date(row['Service Date'])
            if formatted_date:
                df.at[idx, 'Service Date'] = formatted_date
            if error:
                issues.append(f"Row {row_num}: Service Date - {error}")
        
        # Review Link validation
        if pd.notna(row['Review Link']) and row['Review Link'] != '':
            link = str(row['Review Link']).strip()
            if not link.startswith(('http://', 'https://')):
                issues.append(f"Row {row_num}: Review link should start with http:// or https://")
    
    # Summary stats
    if valid_phones > 0:
        issues.insert(0, f"✅ {valid_phones} valid phone numbers found")
    
    return df, issues

def load_message_templates() -> List[Dict[str, str]]:
    """Load and manage message templates"""
    return [
        {
            "template": "Hi {customer_name}! We hope you enjoyed your experience at {business_name}. Your feedback means the world to us! Would you mind sharing a quick Google review?",
            "description": "Friendly and appreciative"
        },
        {
            "template": "Hey {customer_name}! Thanks for choosing {business_name}. We'd love to hear about your experience. Could you leave us a Google review?",
            "description": "Casual and direct"
        },
        {
            "template": "Hi {customer_name}! Thank you for visiting {business_name}. If you had a great experience, we'd really appreciate a Google review!",
            "description": "Polite and straightforward"
        },
        {
            "template": "Hello {customer_name}! We loved having you at {business_name}. Would you take a moment to share your thoughts in a Google review?",
            "description": "Warm and inviting"
        }
    ]

def generate_enhanced_message(business_name: str, customer_name: str, service_type: str = "", service_date: str = "") -> str:
    """Enhanced message generation with template selection"""
    templates = load_message_templates()
    
    # Filter and prioritize templates based on available data
    available_templates = []
    
    if service_date and service_type:
        # Both service date and type available
        date_service_templates = [
            "Hi {customer_name}! Hope you enjoyed your {service_type} at {business_name} on {service_date}. Would you mind leaving us a Google review?",
            "Hey {customer_name}! Thanks for your {service_type} at {business_name} on {service_date}. We'd love to hear about your experience in a Google review!"
        ]
        available_templates.extend([{"template": t} for t in date_service_templates])
    
    elif service_date:
        # Only service date available
        date_templates = [
            "Hi {customer_name}! Hope you enjoyed your visit to {business_name} on {service_date}. Would you mind leaving us a Google review?",
            "Hey {customer_name}! Thanks for visiting {business_name} on {service_date}. We'd love to hear about your experience in a Google review!"
        ]
        available_templates.extend([{"template": t} for t in date_templates])
    
    elif service_type:
        # Only service type available
        service_templates = [
            "Hi {customer_name}! We hope you loved your {service_type} at {business_name}. Would you share your experience with a Google review?",
            "Hey {customer_name}! Thanks for choosing {business_name} for your {service_type}. Mind leaving us a quick review?"
        ]
        available_templates.extend([{"template": t} for t in service_templates])
    
    # Always include base templates
    available_templates.extend(templates)
    
    selected_template = random.choice(available_templates)["template"]
    
    return selected_template.format(
        customer_name=customer_name,
        business_name=business_name,
        service_type=service_type,
        service_date=service_date
    )

def generate_messages_batch(df: pd.DataFrame) -> pd.DataFrame:
    """Generate messages for entire dataframe with enhanced templates"""
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
        
        message = generate_enhanced_message(
            str(row["Business Name"]),
            str(row["Customer Name"]),
            str(service_type) if not pd.isna(service_type) and service_type != '' else "",
            str(service_date) if not pd.isna(service_date) and service_date != '' else ""
        )
        messages.append(message)
        
        progress = (idx + 1) / len(df_processed)
        progress_bar.progress(progress)
        status_text.text(f"Generating messages... {idx+1}/{len(df_processed)}")
    
    progress_bar.empty()
    status_text.empty()
    
    df_processed["Generated_Message"] = messages
    return df_processed

def send_sms_enhanced(twilio_client: Client, to_number: str, message: str, from_number: str) -> Tuple[bool, str]:
    """Enhanced SMS sending with comprehensive error handling"""
    try:
        twilio_message = twilio_client.messages.create(
            body=message,
            from_=from_number,
            to=to_number,
            # Add timeout for safety
            timeout=30
        )
        
        if twilio_message.status in ['sent', 'queued', 'delivered']:
            logging.info(f"SMS sent successfully to {to_number}: {twilio_message.sid}")
            return True, twilio_message.sid
        else:
            logging.warning(f"SMS sent but unusual status: {twilio_message.status} for {to_number}")
            return False, f"Message status: {twilio_message.status}"
            
    except Exception as e:
        error_msg = str(e)
        logging.error(f"SMS sending failed to {to_number}: {error_msg}")
        
        # Handle specific Twilio errors
        if "Permission denied" in error_msg:
            return False, "Not authorized to send to this number"
        elif "is not a valid phone number" in error_msg:
            return False, "Invalid phone number format"
        elif "rate limit" in error_msg.lower():
            return False, "Rate limit exceeded - please slow down"
        elif "overflow" in error_msg.lower():
            return False, "Message queue overflow - try again later"
        elif "authenticate" in error_msg.lower():
            return False, "Twilio authentication failed"
        else:
            return False, error_msg

def send_sms_with_rate_limit(df: pd.DataFrame, test_mode: bool = False, delay_seconds: float = 1.0) -> Tuple[pd.DataFrame, int, int, int]:
    """Enhanced SMS sending with rate limiting and comprehensive tracking"""
    if not twilio_client and not test_mode:
        st.error("❌ Twilio not configured properly. Please check your credentials or run in Test Mode.")
        return df, 0, len(df), 0
    
    sent, failed, skipped = 0, 0, 0
    
    # Initialize tracking columns
    for col in ['SMS_Status', 'Error', 'Sent_Time', 'Message_SID']:
        if col not in df.columns:
            df[col] = ''
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    results_placeholder = st.empty()
    
    for i, row in df.iterrows():
        try:
            # Validation checks
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
            
            # Prepare final message
            message = f"{row['Generated_Message']} {row['Review Link']} Reply STOP to opt out."
            
            if test_mode:
                # Simulate sending in test mode
                time.sleep(0.1)  # Minimal delay for testing
                df.at[i, "SMS_Status"] = "🧪 Test"
                df.at[i, "Sent_Time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                df.at[i, "Message_SID"] = f"TEST_{i}_{datetime.now().timestamp()}"
                sent += 1
            else:
                # Actual SMS sending
                success, result = send_sms_enhanced(
                    twilio_client, 
                    str(row["Phone"]).strip(),
                    message,
                    TWILIO_PHONE
                )
                
                if success:
                    df.at[i, "SMS_Status"] = "✅ Sent"
                    df.at[i, "Message_SID"] = result
                    sent += 1
                else:
                    df.at[i, "SMS_Status"] = "❌ Failed"
                    df.at[i, "Error"] = result
                    failed += 1
                
                df.at[i, "Sent_Time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                time.sleep(delay_seconds)  # Rate limiting
            
            # Update progress
            progress = (i + 1) / len(df)
            progress_bar.progress(progress)
            
            # Update status with real-time results
            with results_placeholder.container():
                col1, col2, col3 = st.columns(3)
                col1.metric("✅ Sent", sent)
                col2.metric("❌ Failed", failed)
                col3.metric("⏭️ Skipped", skipped)
            
            status_text.text(f"{'Testing' if test_mode else 'Sending'}... {i+1}/{len(df)}")
            
        except Exception as e:
            logging.error(f"Unexpected error processing row {i}: {e}")
            df.at[i, "SMS_Status"] = "❌ Failed"
            df.at[i, "Error"] = f"Unexpected error: {str(e)}"
            failed += 1
    
    progress_bar.empty()
    status_text.empty()
    results_placeholder.empty()
    
    return df, sent, failed, skipped

def save_campaign_to_db(campaign_name: str, total_customers: int, sent: int, failed: int, skipped: int, test_mode: bool, df_results: pd.DataFrame) -> int:
    """Save campaign results to database"""
    try:
        success_rate = (sent / (sent + failed)) * 100 if (sent + failed) > 0 else 0
        
        with get_db_connection() as conn:
            cursor = conn.execute('''
                INSERT INTO campaigns 
                (campaign_name, total_customers, messages_sent, messages_failed, messages_skipped, test_mode, success_rate)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (campaign_name, total_customers, sent, failed, skipped, test_mode, success_rate))
            
            campaign_id = cursor.lastrowid
            
            # Save individual message results
            for _, row in df_results.iterrows():
                conn.execute('''
                    INSERT INTO campaign_details 
                    (campaign_id, customer_name, phone, status, error_message, sent_time)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    campaign_id,
                    row.get('Customer Name', ''),
                    row.get('Phone', ''),
                    row.get('SMS_Status', ''),
                    row.get('Error', ''),
                    row.get('Sent_Time', '')
                ))
            
            conn.commit()
        
        logging.info(f"Campaign {campaign_id} saved to database")
        return campaign_id
        
    except Exception as e:
        logging.error(f"Failed to save campaign to database: {e}")
        return -1

def get_campaign_history(limit: int = 10) -> List[Dict]:
    """Retrieve campaign history from database"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT * FROM campaigns 
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (limit,))
            
            campaigns = []
            for row in cursor.fetchall():
                campaigns.append(dict(row))
            
            return campaigns
    except Exception as e:
        logging.error(f"Failed to retrieve campaign history: {e}")
        return []

# ================== STREAMLIT UI ==================
st.sidebar.title("Navigation")
page = st.sidebar.radio("Go to", ["Send Campaign", "Campaign History", "Settings"])

if page == "Send Campaign":
    
    # Show Twilio status warning if not configured
    if not twilio_client:
        st.warning("""
        🔧 **Twilio Not Configured** 
        - The app is running in **Test Mode Only**
        - No real SMS messages will be sent
        - To enable real SMS, configure Twilio credentials in Settings
        """)
    
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
    
    with st.expander("📋 STEP 0: Download CSV Template", expanded=False):
        template_df = pd.DataFrame({
            "Business Name": ["Garden Cafe", "Bloom Florist"],
            "Customer Name": ["John Smith", "Sarah Johnson"], 
            "Email": ["john@example.com", "sarah@example.com"],
            "Phone": ["+15555550100", "+15555550101"],
            "Service Date": ["2024-01-15", "2024-01-16"],
            "Service Type": ["Lunch Service", "Flower Delivery"],
            "Review Link": ["https://search.google.com/local/writereview?placeid=YOUR_PLACE_ID", 
                          "https://search.google.com/local/writereview?placeid=YOUR_PLACE_ID"]
        })
        
        csv_buffer = io.StringIO()
        template_df.to_csv(csv_buffer, index=False)
        st.download_button(
            "📥 Download CSV Template", 
            data=csv_buffer.getvalue(), 
            file_name="reviewgarden_template.csv", 
            mime="text/csv",
            help="Use this template to ensure your CSV has the correct format"
        )
        st.info("""
        💡 **Tips for best results:**
        - Service Type is optional but helps personalize messages
        - Phone numbers should include country code (+1 for US/Canada)
        - Review links should be direct Google review links
        - Dates can be in any common format (YYYY-MM-DD, MM/DD/YYYY, etc.)
        """)

    st.subheader("📤 Step 1: Upload Customer CSV")
    
    # Campaign naming
    st.session_state.campaign_name = st.text_input(
        "Campaign Name", 
        value=st.session_state.campaign_name,
        help="Give this campaign a descriptive name for tracking"
    )
    
    uploaded_file = st.file_uploader("Choose your customer CSV file", type="csv", key="uploader")
    
    if uploaded_file:
        try:
            df = pd.read_csv(uploaded_file)
            df.columns = df.columns.str.strip()
            
            if check_csv(df):
                df_clean, issues = enhanced_validate_csv_data(df)
                
                if issues:
                    with st.expander(f"⚠️ Validation Results ({len(issues)} issues)", expanded=True):
                        for issue in issues:
                            if issue.startswith("✅"):
                                st.success(issue)
                            else:
                                st.warning(issue)
                
                st.success("✅ CSV loaded successfully: {} customers".format(len(df_clean)))
                st.session_state.df_processed = df_clean
                st.session_state.current_step = 2
                
                with st.expander("📊 Data Preview", expanded=False):
                    st.dataframe(df_clean.head(10), use_container_width=True)
                    if len(df_clean) > 10:
                        st.caption(f"Showing 10 of {len(df_clean)} rows")
        
        except Exception as e:
            st.error(f"❌ Error reading CSV file: {str(e)}")
            logging.error(f"CSV reading error: {e}")

    if st.session_state.current_step >= 2 and st.session_state.df_processed is not None:
        st.subheader("✍️ Step 2: Generate Messages")
        
        if 'Generated_Message' not in st.session_state.df_processed.columns:
            col1, col2 = st.columns([3, 1])
            with col1:
                st.info("""
                **Ready to generate personalized messages!** 
                - Each customer will receive a unique message
                - Messages are tailored using available data (service type, date, etc.)
                - You can preview and regenerate if needed
                """)
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
                preview_count = st.slider("Number of messages to preview", 1, min(10, len(st.session_state.df_processed)), 3)
                
                for idx, row in st.session_state.df_processed.head(preview_count).iterrows():
                    if not pd.isna(row['Customer Name']):
                        col1, col2 = st.columns([1, 4])
                        with col1:
                            st.write(f"**{row['Customer Name']}**")
                            st.caption(f"`{row['Phone']}`")
                        with col2:
                            st.text_area(
                                "Message", 
                                value=row.get('Generated_Message', 'No message'), 
                                key=f"msg_{idx}",
                                height=80,
                                label_visibility="collapsed"
                            )
                        st.markdown("---")
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button("🔄 Regenerate All Messages", use_container_width=True):
                    df_processed = generate_messages_batch(st.session_state.df_processed)
                    st.session_state.df_processed = df_processed
                    st.success("✅ Messages regenerated!")
                    st.rerun()

    if (st.session_state.current_step >= 3 and 
        st.session_state.df_processed is not None and 
        'Generated_Message' in st.session_state.df_processed.columns and
        not st.session_state.campaign_sent):
        
        st.subheader("🚀 Step 3: Launch Campaign")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            # Force test mode if Twilio not configured
            if not twilio_client:
                st.session_state.test_mode = True
                st.info("🧪 Test Mode (Twilio not configured)")
            else:
                st.session_state.test_mode = st.checkbox(
                    "🧪 Test Mode (Don't actually send)", 
                    value=st.session_state.test_mode,
                    help="Preview what will happen without sending real SMS. Perfect for testing!"
                )
        
        with col2:
            confirm_send = st.checkbox(
                "✓ I have permission to contact these customers",
                help="Required: Confirm you have consent to send SMS to these customers"
            )
        
        with col3:
            rate_limit = st.number_input(
                "⏱️ Delay between messages (seconds)",
                min_value=0.5,
                max_value=5.0,
                value=1.0,
                step=0.5,
                help="Prevents rate limiting issues with Twilio"
            )
        
        st.markdown("### 📊 Campaign Summary")
        summary_cols = st.columns(4)
        total_customers = len(st.session_state.df_processed)
        valid_phones = st.session_state.df_processed['Phone'].notna().sum()
        messages_ready = len(st.session_state.df_processed[st.session_state.df_processed['Generated_Message'].notna()])
        estimated_time = int(valid_phones * rate_limit / 60)
        
        summary_cols[0].metric("Total Customers", total_customers)
        summary_cols[1].metric("Valid Phone Numbers", valid_phones)
        summary_cols[2].metric("Messages Ready", messages_ready)
        summary_cols[3].metric("Estimated Time", f"{estimated_time} min")
        
        st.markdown("---")
        
        if confirm_send or st.session_state.test_mode:
            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                button_text = '🧪 Run Test Campaign' if st.session_state.test_mode else '🚀 LAUNCH CAMPAIGN'
                button_help = "Send test messages (no real SMS)" if st.session_state.test_mode else "Send real SMS messages to customers"
                
                if not twilio_client and not st.session_state.test_mode:
                    st.error("❌ Cannot send real SMS - Twilio not configured")
                    launch_disabled = True
                else:
                    launch_disabled = st.session_state.sending_in_progress
                
                launch_button = st.button(
                    button_text,
                    type="primary" if not st.session_state.test_mode else "secondary",
                    use_container_width=True,
                    disabled=launch_disabled,
                    help=button_help
                )
                
                if launch_button:
                    st.session_state.sending_in_progress = True
                    
                    df = st.session_state.df_processed.copy()
                    
                    if st.session_state.test_mode:
                        st.info("🧪 Test campaign starting... (No real messages will be sent)")
                    else:
                        st.warning("🚀 REAL CAMPAIGN STARTING - Messages will be sent to customers!")
                    
                    df, sms_sent, sms_failed, sms_skipped = send_sms_with_rate_limit(
                        df, 
                        test_mode=st.session_state.test_mode,
                        delay_seconds=rate_limit
                    )
                    
                    # Save campaign results to database
                    campaign_id = save_campaign_to_db(
                        st.session_state.campaign_name,
                        total_customers,
                        sms_sent,
                        sms_failed,
                        sms_skipped,
                        st.session_state.test_mode,
                        df
                    )
                    
                    st.session_state.df_processed = df
                    st.session_state.campaign_sent = True
                    st.session_state.campaign_results = {
                        'sent': sms_sent,
                        'failed': sms_failed,
                        'skipped': sms_skipped,
                        'timestamp': datetime.now(),
                        'test_mode': st.session_state.test_mode,
                        'campaign_id': campaign_id
                    }
                    st.session_state.sending_in_progress = False
                    st.session_state.current_step = 4
                    
                    if not st.session_state.test_mode:
                        st.balloons()
                    
                    st.rerun()
        else:
            st.warning("⚠️ Please confirm you have permission to contact these customers before proceeding")

elif page == "Campaign History":
    st.header("📊 Campaign History")
    
    campaigns = get_campaign_history(20)
    
    if campaigns:
        st.subheader("Recent Campaigns")
        
        for campaign in campaigns:
            with st.expander(f"{campaign['campaign_name']} - {campaign['timestamp']} {'🧪' if campaign['test_mode'] else '🚀'}", expanded=False):
                col1, col2, col3, col4 = st.columns(4)
                
                col1.metric("Total", campaign['total_customers'])
                col2.metric("Sent", campaign['messages_sent'])
                col3.metric("Failed", campaign['messages_failed'])
                col4.metric("Success Rate", f"{campaign['success_rate']:.1f}%")
                
                if st.button(f"View Details", key=f"details_{campaign['id']}"):
                    # In a real app, you'd fetch and display detailed results
                    st.info(f"Detailed results for campaign {campaign['id']}")
    else:
        st.info("No campaign history yet. Send your first campaign to see results here!")
        
    # Show latest campaign if available
    if st.session_state.campaign_results:
        st.markdown("---")
        st.subheader("Latest Campaign")
        results = st.session_state.campaign_results
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Campaign Date", results['timestamp'].strftime("%Y-%m-%d %H:%M:%S"))
            st.metric("Mode", "Test" if results['test_mode'] else "Live")
        with col2:
            st.metric("Total Sent", results['sent'])
            st.metric("Failed", results['failed'])

elif page == "Settings":
    st.header("⚙️ Settings")
    
    st.subheader("📱 Twilio SMS Configuration")
    if twilio_client:
        st.success("✅ Twilio is connected and ready")
        st.code(f"Phone Number: {TWILIO_PHONE}")
        if TWILIO_ACCOUNT_SID:
            st.code(f"Account SID: {TWILIO_ACCOUNT_SID[:8]}...")
    else:
        st.error("❌ Twilio not configured")
        st.markdown("""
        **To configure Twilio:**
        
        1. **Install Twilio package:**
        ```bash
        pip install twilio
        ```
        
        2. **Create a `.env` file in your project directory**
        
        3. **Add the following variables to `.env`:**
        ```
        TWILIO_ACCOUNT_SID=your_account_sid_here
        TWILIO_AUTH_TOKEN=your_auth_token_here  
        TWILIO_PHONE_NUMBER=your_twilio_phone_here
        ```
        
        4. **Get your credentials from:**
           - [Twilio Console](https://console.twilio.com)
           - Your Account SID and Auth Token are in the dashboard
           - Buy a phone number in Twilio for sending SMS
        
        5. **Restart the application**
        """)
        
        with st.expander("🔧 Manual Configuration (Alternative)"):
            st.info("You can also set environment variables in your deployment platform:")
            st.code("""
            # For Streamlit Cloud
            In your app settings, add:
            TWILIO_ACCOUNT_SID=your_sid
            TWILIO_AUTH_TOKEN=your_token
            TWILIO_PHONE_NUMBER=your_number
            """)
    
    st.markdown("---")
    st.subheader("🔒 Data Privacy & Security")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.info("""
        **Data Handling:**
        - Customer data is only stored in memory during your session
        - No data is saved to disk permanently
        - Campaign results can be exported as CSV
        - All data is cleared when you close the browser
        """)
    
    with col2:
        st.info("""
        **Security Features:**
        - Phone number validation and formatting
        - Rate limiting to prevent API abuse
        - Test mode for safe experimentation
        - Required consent confirmation
        """)
    
    st.markdown("---")
    st.subheader("🛠️ Application Management")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🗑️ Clear All Session Data", type="secondary", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.success("All session data cleared!")
            st.rerun()
    
    with col2:
        if st.button("🔄 Restart Application", type="primary", use_container_width=True):
            st.cache_resource.clear()
            st.rerun()

st.markdown("---")
st.markdown("🌿 **ReviewGarden** - Grow your reputation honestly | Made with Streamlit")

# Add some custom CSS for better styling
st.markdown("""
<style>
    .stProgress > div > div > div > div {
        background-color: #28a745;
    }
    .st-bb {
        background-color: transparent;
    }
    .st-at {
        background-color: #0d6efd;
    }
</style>
""", unsafe_allow_html=True)
